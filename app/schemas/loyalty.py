# app/schemas/loyalty.py
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel


class LoyaltyBalanceOut(BaseModel):
    balance: int
    # 2026-11 會員等級（跟 mynotification plan-member-tier.md 對過的契約），
    # 每次查詢即時計算，不存 DB；只加欄位，舊版 App 不受影響
    tier: Literal["regular", "silver", "gold"]
    year_spend: int  # 今年（台灣時間）累計實付消費
    next_tier: Optional[Literal["silver", "gold"]] = None  # 金卡為 null
    amount_to_next_tier: Optional[int] = None  # 金卡為 null
    tier_expires_at: Optional[date] = None  # 一般會員為 null
    earn_multiplier: float


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
