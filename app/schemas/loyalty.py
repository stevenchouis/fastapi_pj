# app/schemas/loyalty.py
from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel

from app.schemas.coupon import CouponOut


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


class ExchangeOptionOut(BaseModel):
    id: str  # 例如 "coupon_100"，POST /loyalty/exchange 的 option_id
    title: str
    points_cost: int
    discount_amount: int
    valid_days: int


class ExchangeOptionsOut(BaseModel):
    options: List[ExchangeOptionOut]
    monthly_limit: int
    used_this_month: int


class ExchangeRequest(BaseModel):
    option_id: str


class ExchangeOut(BaseModel):
    coupon: CouponOut  # 格式同 /coupons/me
    balance: int  # 扣完的點數餘額


class MemberCodeOut(BaseModel):
    # 明文碼只在這次回應中出現一次，資料庫只存 hash（做法比照 CouponRedeemCodeOut）
    code: str
    expires_at: datetime


class LoyaltyTransactionOut(BaseModel):
    id: int
    type: Literal["earn", "redeem", "expire", "reverse_earn", "reverse_redeem", "exchange"]
    amount: int
    reason: str
    related_order_id: Optional[int] = None
    related_dine_in_order_id: Optional[int] = None
    related_store_checkout_id: Optional[int] = None
    related_coupon_id: Optional[int] = None  # 只有 type="exchange" 會有值
    restaurant_id: Optional[int] = None
    created_at: datetime
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True  # 允許從 SQLAlchemy 模型轉換
