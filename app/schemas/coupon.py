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
