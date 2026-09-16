# app/schemas/loyalty.py
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class LoyaltyBalanceOut(BaseModel):
    balance: int


class MemberCodeOut(BaseModel):
    # 明文碼只在這次回應中出現一次，資料庫只存 hash（做法比照 CouponRedeemCodeOut）
    code: str
    expires_at: datetime


class LoyaltyTransactionOut(BaseModel):
    id: int
    type: Literal["earn", "redeem", "expire", "reverse_earn", "reverse_redeem"]
    amount: int
    reason: str
    related_order_id: Optional[int] = None
    related_dine_in_order_id: Optional[int] = None
    related_store_checkout_id: Optional[int] = None
    restaurant_id: Optional[int] = None
    created_at: datetime
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True  # 允許從 SQLAlchemy 模型轉換
