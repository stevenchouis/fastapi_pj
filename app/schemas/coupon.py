# app/schemas/coupon.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class CouponOut(BaseModel):
    id: int
    user_id: int
    title: str
    discount_amount: float
    is_used: bool
    used_at: Optional[datetime] = None
    expired_at: datetime
    restaurant_id: Optional[int] = None

    class Config:
        from_attributes = True  # 允許從 SQLAlchemy 模型轉換


class CouponRedeemCodeOut(BaseModel):
    code: str
    expires_at: datetime


class CouponRedeemRequest(BaseModel):
    code: str


class CouponRedeemResult(BaseModel):
    id: int
    title: str
    discount_amount: float

    class Config:
        from_attributes = True


class AdminIssueCouponRequest(BaseModel):
    user_email: EmailStr
    title: str
    discount_amount: float = Field(gt=0)
    valid_days: int = Field(default=30, gt=0)
    # 純記錄用途（2026-09 多門市支援）：標記這張手動發的券是哪個門市的活動加碼/
    # 客訴補償，不給就是連鎖層級。不影響核銷——任何門市店員都能核銷。
    restaurant_id: Optional[int] = None
