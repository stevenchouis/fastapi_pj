# app/api/v1/endpoints/coupons.py
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps  # 假設你這裡有用於獲取 current_user 的 dependency
from app.database_async import AsyncSessionLocal, get_db  #
from app.models import Coupon, User  #
from app.schemas.coupon import (  # 需定義回傳格式
    AdminIssueCouponRequest,
    CouponOut,
    CouponRedeemCodeOut,
    CouponRedeemRequest,
    CouponRedeemResult,
)
from app.services.coupon_service import build_coupon
from app.services.push_service import send_user_push_notifications

router = APIRouter()

REDEEM_CODE_EXPIRE_MINUTES = 10


@router.get("/me", response_model=List[CouponOut])
async def get_my_coupons(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),  # 獲取當前登入者
):
    """
    獲取當前登入使用者的優惠券列表
    """
    # 篩選屬於該 user_id 的優惠券
    query = select(Coupon).where(Coupon.user_id == current_user.id)
    result = await db.execute(query)
    coupons = result.scalars().all()

    return coupons


@router.post("/{coupon_id}/redeem-code", response_model=CouponRedeemCodeOut)
async def create_redeem_code(
    coupon_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    產生一組限時核銷碼（10 分鐘效期、單次使用）。
    只存 hash 到資料庫，明文碼只在這次回應中出現一次。
    """
    query = select(Coupon).where(
        Coupon.id == coupon_id, Coupon.user_id == current_user.id
    )
    result = await db.execute(query)
    coupon = result.scalars().first()
    if not coupon:
        raise HTTPException(status_code=404, detail="優惠券不存在")

    now = datetime.now(UTC)
    if coupon.is_used:
        raise HTTPException(status_code=400, detail="此優惠券已被使用")
    if coupon.expired_at < now:
        raise HTTPException(status_code=400, detail="此優惠券已過期")

    # 6 位數字碼只有 100 萬種組合，只跟「目前仍有效」的核銷碼比對是否重複即可，
    # 已過期的舊碼雜湊值相同也沒關係
    for _ in range(5):
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        dup_query = select(Coupon.id).where(
            Coupon.redeem_code_hash == code_hash,
            Coupon.redeem_code_expires_at > now,
        )
        dup_result = await db.execute(dup_query)
        if not dup_result.first():
            break
    else:
        raise HTTPException(status_code=500, detail="核銷碼產生失敗，請重試")

    expires_at = now + timedelta(minutes=REDEEM_CODE_EXPIRE_MINUTES)
    coupon.redeem_code_hash = code_hash
    coupon.redeem_code_expires_at = expires_at

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 核銷碼寫入失敗: {e}")
        raise HTTPException(status_code=500, detail="核銷碼產生失敗")

    return CouponRedeemCodeOut(code=code, expires_at=expires_at)


@router.post("/redeem", response_model=CouponRedeemResult)
async def redeem_coupon(
    payload: CouponRedeemRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    用核銷碼核銷優惠券。目前仍是消費者用自己的 JWT 呼叫（人工核銷過渡方案），
    不檢查優惠券擁有者是否為 current_user —— 之後改為店員角色呼叫時這裡不用改。
    """
    code_hash = hashlib.sha256(payload.code.encode()).hexdigest()
    now = datetime.now(UTC)

    # 原子性 UPDATE，避免同一組核銷碼被同時打兩次而重複核銷
    statement = (
        update(Coupon)
        .where(Coupon.redeem_code_hash == code_hash)
        .where(Coupon.is_used.is_(False))
        .where(Coupon.redeem_code_expires_at > now)
        .values(
            is_used=True,
            used_at=now,
            redeem_code_hash=None,
            redeem_code_expires_at=None,
        )
        .returning(Coupon.id, Coupon.title, Coupon.discount_amount)
    )

    try:
        result = await db.execute(statement)
        row = result.first()
        if row is None:
            await db.rollback()
            raise HTTPException(status_code=400, detail="核銷碼無效或已過期")
        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 核銷失敗: {e}")
        raise HTTPException(status_code=500, detail="核銷失敗")

    return CouponRedeemResult(id=row[0], title=row[1], discount_amount=row[2])


@router.post(
    "/admin/issue",
    response_model=CouponOut,
    dependencies=[Depends(deps.verify_admin_key)],
    include_in_schema=False,  # 不對外公開在 Swagger /docs，只給知道路徑跟 Admin Key 的人用
)
async def admin_issue_coupon(
    payload: AdminIssueCouponRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """管理者手動發券：活動加碼、客訴補償用。用 X-Admin-Key header 保護，不走一般使用者 JWT。"""
    result = await db.execute(select(User).where(User.email == payload.user_email))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="找不到此 email 對應的使用者")
    user_id = user.id  # 先取出來，避免 commit 後 user 物件過期，存取屬性觸發非同步例外

    coupon = build_coupon(
        user_id=user_id,
        title=payload.title,
        discount_amount=payload.discount_amount,
        valid_days=payload.valid_days,
    )

    try:
        db.add(coupon)
        await db.commit()
        await db.refresh(coupon)
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 管理者發券失敗: {e}")
        raise HTTPException(status_code=500, detail="發券失敗")

    # 跟一般使用者流程一樣，透過 BackgroundTask 寫通知紀錄 + 觸發真實 Expo 推播
    background_tasks.add_task(
        send_user_push_notifications,
        AsyncSessionLocal,
        user_id,
        "🎁 您收到一張優惠券",
        f"「{coupon.title}」已存入您的帳戶",
        {"coupon_id": coupon.id, "screen": "Coupons"},
    )

    return coupon
