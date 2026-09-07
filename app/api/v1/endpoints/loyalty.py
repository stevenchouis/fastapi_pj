# app/api/v1/endpoints/loyalty.py
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import LoyaltyTransaction
from app.schemas.loyalty import LoyaltyBalanceOut, LoyaltyTransactionOut

router = APIRouter()


@router.get("/me", response_model=LoyaltyBalanceOut)
async def get_my_loyalty_balance(
    current_user=Depends(deps.get_current_user),
):
    """回傳目前登入使用者的點數餘額，直接讀 User.loyalty_balance（權威資料）。"""
    return LoyaltyBalanceOut(balance=current_user.loyalty_balance)


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
