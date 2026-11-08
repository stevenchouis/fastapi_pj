# app/api/v1/endpoints/loyalty.py
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import Coupon, LoyaltyTransaction, User
from app.schemas.coupon import CouponOut
from app.schemas.loyalty import (
    ExchangeOptionOut,
    ExchangeOptionsOut,
    ExchangeOut,
    ExchangeRequest,
    LoyaltyBalanceOut,
    LoyaltyTransactionOut,
    MemberCodeOut,
)
from app.services import loyalty_service

router = APIRouter()

MEMBER_CODE_EXPIRE_MINUTES = 10


@router.get("/me", response_model=LoyaltyBalanceOut)
async def get_my_loyalty_balance(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    回傳目前登入使用者的點數餘額（直接讀 User.loyalty_balance，權威資料），
    以及 2026-11 會員等級資訊（loyalty_service.get_member_tier_info 即時計算）。
    """
    tier_info = await loyalty_service.get_member_tier_info(db, current_user.id)
    return LoyaltyBalanceOut(balance=current_user.loyalty_balance, **tier_info)


@router.get("/exchange-options", response_model=ExchangeOptionsOut)
async def get_exchange_options(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """點數兌換優惠券目錄（寫死在 loyalty_service.EXCHANGE_OPTIONS）＋本月已兌換張數。"""
    options = [
        ExchangeOptionOut(
            id=option_id,
            title=o["title"],
            points_cost=o["points_cost"],
            discount_amount=o["discount_amount"],
            valid_days=loyalty_service.EXCHANGE_COUPON_VALID_DAYS,
        )
        for option_id, o in loyalty_service.EXCHANGE_OPTIONS.items()
    ]
    used = await loyalty_service.count_exchanges_this_month(db, current_user.id)
    return ExchangeOptionsOut(
        options=options,
        monthly_limit=loyalty_service.EXCHANGE_MONTHLY_LIMIT,
        used_this_month=used,
    )


@router.post("/exchange", response_model=ExchangeOut, status_code=201)
async def exchange_points(
    payload: ExchangeRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    用點數兌換一張優惠券（2026-11，plan-points-exchange.md）。鎖使用者列→檢查月上限→
    建券→FIFO 扣點（明細 type="exchange"）都在同一個 transaction，任一步失敗整筆 rollback，
    不會出現「扣了點沒拿到券」或連點換到兩張。兌換後不能退回點數。
    錯誤格式比照結帳折抵：detail 是帶 error_code 的物件。
    """
    if payload.option_id not in loyalty_service.EXCHANGE_OPTIONS:
        raise HTTPException(status_code=404, detail="兌換品項不存在")

    user_id = current_user.id
    try:
        outcome, coupon = await loyalty_service.exchange_points_for_coupon(
            db, user_id, payload.option_id
        )
        if outcome == "limit":
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "exchange_limit_reached",
                    "message": f"本月已兌換 {loyalty_service.EXCHANGE_MONTHLY_LIMIT} 張，請下個月再來",
                },
            )
        if outcome == "insufficient":
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail={"error_code": "insufficient_points", "message": "點數餘額不足"},
            )
        # commit 後物件屬性會過期（MissingGreenlet），id 先存成區域變數
        coupon_id = coupon.id
        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 點數兌換失敗: {e}")
        raise HTTPException(status_code=500, detail="點數兌換失敗")

    coupon = (await db.execute(select(Coupon).where(Coupon.id == coupon_id))).scalar_one()
    balance = (
        await db.execute(select(User.loyalty_balance).where(User.id == user_id))
    ).scalar_one()
    return ExchangeOut(coupon=CouponOut.model_validate(coupon), balance=balance)


@router.post("/member-code", response_model=MemberCodeOut)
async def create_member_code(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    產生一組限時會員辨識碼（門市收銀用店員掃碼/輸入辨識身份），做法完全比照
    coupons.py 的 create_redeem_code：6 位數字、10 分鐘效期、只存 hash，明文碼
    只在這次回應中出現一次。使用者手動按按鈕產生/重新產生，不是自動輪替——
    重新產生會直接覆蓋掉舊的（舊碼立即失效）。
    """
    now = datetime.now(UTC)

    for _ in range(5):
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        dup_query = select(User.id).where(
            User.member_code_hash == code_hash,
            User.member_code_expires_at > now,
        )
        dup_result = await db.execute(dup_query)
        if not dup_result.first():
            break
    else:
        raise HTTPException(status_code=500, detail="會員碼產生失敗，請重試")

    expires_at = now + timedelta(minutes=MEMBER_CODE_EXPIRE_MINUTES)
    current_user.member_code_hash = code_hash
    current_user.member_code_expires_at = expires_at

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 會員碼寫入失敗: {e}")
        raise HTTPException(status_code=500, detail="會員碼產生失敗")

    return MemberCodeOut(code=code, expires_at=expires_at)


@router.get("/transactions", response_model=List[LoyaltyTransactionOut])
async def get_my_loyalty_transactions(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """取得目前登入使用者的點數明細（賺取/折抵/過期紀錄），新到舊排序。"""
    query = (
        select(LoyaltyTransaction)
        .where(LoyaltyTransaction.user_id == current_user.id)
        .order_by(LoyaltyTransaction.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()
