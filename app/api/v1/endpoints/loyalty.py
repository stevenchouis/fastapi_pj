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
from app.models import LoyaltyTransaction, User
from app.schemas.loyalty import LoyaltyBalanceOut, LoyaltyTransactionOut, MemberCodeOut

router = APIRouter()

MEMBER_CODE_EXPIRE_MINUTES = 10


@router.get("/me", response_model=LoyaltyBalanceOut)
async def get_my_loyalty_balance(
    current_user=Depends(deps.get_current_user),
):
    """回傳目前登入使用者的點數餘額，直接讀 User.loyalty_balance（權威資料）。"""
    return LoyaltyBalanceOut(balance=current_user.loyalty_balance)


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
