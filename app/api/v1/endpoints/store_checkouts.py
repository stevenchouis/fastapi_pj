# app/api/v1/endpoints/store_checkouts.py
import hashlib
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import AsyncSessionLocal, get_db
from app.models import Coupon, StoreCheckout, User
from app.schemas.store_checkout import (
    MemberCodeLookupOut,
    MemberCodeLookupRequest,
    StoreCheckoutCreate,
    StoreCheckoutOut,
)
from app.services import coupon_service, loyalty_service
from app.services.push_service import send_user_push_notifications

router = APIRouter()


def _hash_member_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _to_store_checkout_out(checkout: StoreCheckout) -> StoreCheckoutOut:
    # points_earned 是算出來的（不是存在 DB 的欄位），付款當下就是最終值——
    # 門市收銀的現金/街口支付視為當下已完成付款，沒有像 Order 那樣要等
    # callback 確認的中繼態，所以這裡永遠回實際已入帳的點數，不需要看 status
    points_earned = loyalty_service.calc_earned_points(checkout.total_amount)
    return StoreCheckoutOut(
        id=checkout.id,
        user_id=checkout.user_id,
        staff_user_id=checkout.staff_user_id,
        restaurant_id=checkout.restaurant_id,
        subtotal=float(checkout.subtotal),
        payment_method=checkout.payment_method,
        coupon_id=checkout.coupon_id,
        coupon_discount=float(checkout.coupon_discount),
        points_used=checkout.points_used,
        points_discount=float(checkout.points_discount),
        total_amount=float(checkout.total_amount),
        points_earned=points_earned,
        status=checkout.status,
        created_at=checkout.created_at,
    )


@router.post("/lookup", response_model=MemberCodeLookupOut)
async def lookup_member_code(
    payload: MemberCodeLookupRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """
    店員掃碼/輸入會員碼後的「預覽」——只查不消費，讓店員在送出結帳前能看到
    能不能用點數/券折抵。真正標記會員碼已使用是 POST /store-checkouts 送出
    結帳當下才會發生，這裡查完之後店員還可以反悔、不會因為多看一次就把碼燒掉。
    """
    now = datetime.now(UTC)
    code_hash = _hash_member_code(payload.member_code)
    query = select(User).where(
        User.member_code_hash == code_hash,
        User.member_code_expires_at > now,
    )
    result = await db.execute(query)
    user = result.scalars().first()
    if user is None:
        raise HTTPException(status_code=404, detail="會員碼無效或已過期")

    coupon_query = select(Coupon).where(
        Coupon.user_id == user.id,
        Coupon.is_used.is_(False),
        Coupon.expired_at > now,
    )
    coupon_result = await db.execute(coupon_query)
    coupons = coupon_result.scalars().all()

    return MemberCodeLookupOut(
        user_id=user.id,
        email=user.email,
        loyalty_balance=user.loyalty_balance,
        coupons=coupons,
    )


@router.post("", response_model=StoreCheckoutOut, status_code=201)
async def create_store_checkout(
    payload: StoreCheckoutCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """
    門市收銀送出結帳。現金/街口支付都視為當下已完成付款（沒有像 ECPay 那樣的
    非同步中繼狀態），所以不需要 pending 狀態或取消/退還機制——這點务必留意：
    如果之後街口支付真的要接真實金流（變成非同步叫出付款頁→等 callback），
    要在同一批工作補上跟 POST /orders/{id}/cancel 對等的取消/退還機制，
    不要等真的有使用者卡住才補（CLAUDE.md 有記錄過這個教訓）。

    折抵順序跟 /orders、/dine-in-orders 完全相同：先套用優惠券折扣（clamp 到
    不超過 subtotal），再用「券後金額」計算點數折抵上限；折抵/會員碼消費/
    賺點都跟建立這筆結帳包在同一個 transaction，任一步失敗就整筆 rollback。

    restaurant_id 不接受前端傳入，自動依登入店員的 User.restaurant_id（跟
    tables/menu-items/admin/dine-in-orders 同樣的多門市 scoping 慣例）。
    """
    if current_user.restaurant_id is None:
        raise HTTPException(status_code=400, detail="帳號尚未指定所屬門市")

    now = datetime.now(UTC)
    code_hash = _hash_member_code(payload.member_code)

    try:
        # 原子性 UPDATE 同時驗證會員碼有效性並消費掉（防重放），比照
        # coupons.py /redeem 的做法——用 conditional UPDATE 一步到位，
        # 不會有「先查後用」中間被搶先用掉的競態
        statement = (
            update(User)
            .where(User.member_code_hash == code_hash)
            .where(User.member_code_expires_at > now)
            .values(member_code_hash=None, member_code_expires_at=None)
            .returning(User.id)
        )
        result = await db.execute(statement)
        row = result.first()
        if row is None:
            await db.rollback()
            raise HTTPException(status_code=404, detail="會員碼無效或已過期")
        (member_user_id,) = row

        subtotal = Decimal(payload.subtotal)

        coupon_discount = Decimal("0")
        if payload.coupon_id is not None:
            outcome, discount_amount = await coupon_service.apply_coupon_for_checkout(
                db, member_user_id, payload.coupon_id
            )
            if outcome == "not_found":
                await db.rollback()
                raise HTTPException(status_code=404, detail="優惠券不存在")
            if outcome == "already_used":
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "coupon_already_used",
                        "message": "此優惠券已使用",
                    },
                )
            if outcome == "expired":
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "coupon_expired",
                        "message": "此優惠券已過期",
                    },
                )
            coupon_discount = min(discount_amount, subtotal)

        after_coupon = subtotal - coupon_discount

        points_used = payload.use_points
        points_discount = Decimal("0")
        if points_used > 0:
            max_points = loyalty_service.calc_max_redeemable_points(after_coupon)
            if points_used > max_points:
                await db.rollback()
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error_code": "points_cap_exceeded",
                        "message": f"超過可折抵金額 50% 上限，最多可用 {max_points} 點",
                    },
                )
            points_discount = Decimal(points_used) * loyalty_service.REDEEM_POINT_VALUE

        total_amount = after_coupon - points_discount

        checkout = StoreCheckout(
            user_id=member_user_id,
            staff_user_id=current_user.id,
            restaurant_id=current_user.restaurant_id,
            subtotal=subtotal,
            payment_method=payload.payment_method,
            coupon_id=payload.coupon_id,
            coupon_discount=coupon_discount,
            points_used=points_used,
            points_discount=points_discount,
            total_amount=total_amount,
        )
        db.add(checkout)
        # 先 flush 拿到 id（此時屬性還沒過期），commit 後 session 預設會
        # expire 掉所有屬性，之後再存取 checkout.id 會觸發同步環境下無法完成的
        # 非同步重新查詢（MissingGreenlet），所以要在 commit 前存成區域變數
        await db.flush()
        checkout_id = checkout.id

        if points_used > 0:
            redeemed = await loyalty_service.redeem_points(
                db,
                member_user_id,
                points_used,
                reason=f"折抵：門市收銀 #{checkout_id}",
                related_store_checkout_id=checkout_id,
                restaurant_id=current_user.restaurant_id,
            )
            if not redeemed:
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "insufficient_points",
                        "message": "點數餘額不足",
                    },
                )

        # 現金/街口支付視為當下已完成付款，直接發放消費回饋點數，不像 Order
        # 要等 ECPay callback 確認才發
        earned_points = loyalty_service.calc_earned_points(total_amount)
        if earned_points > 0:
            await loyalty_service.earn_points(
                db,
                member_user_id,
                earned_points,
                reason=f"門市收銀消費回饋：#{checkout_id}",
                related_store_checkout_id=checkout_id,
                restaurant_id=current_user.restaurant_id,
            )

        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 建立門市收銀結帳失敗: {e}")
        raise HTTPException(status_code=500, detail="建立結帳失敗")

    query = select(StoreCheckout).where(StoreCheckout.id == checkout_id)
    result = await db.execute(query)
    checkout = result.scalars().first()

    background_tasks.add_task(
        send_user_push_notifications,
        AsyncSessionLocal,
        member_user_id,
        "mynotification",
        "💳 門市消費已入帳",
        f"本次消費 ${checkout.total_amount} 已完成，點數/優惠券異動可至「我的點數」查看。",
        {
            "type": "store_checkout_completed",
            "screen": "Points",
            "store_checkout_id": checkout_id,
        },
    )

    return _to_store_checkout_out(checkout)
