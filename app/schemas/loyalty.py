# app/schemas/loyalty.py
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class LoyaltyBalanceOut(BaseModel):
    balance: int


class LoyaltyTransactionOut(BaseModel):
    id: int
    type: Literal["earn", "redeem", "expire", "reverse"]
    amount: int
    reason: str
    related_order_id: Optional[int] = None
    related_dine_in_order_id: Optional[int] = None
    created_at: datetime
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True  # 允許從 SQLAlchemy 模型轉換
