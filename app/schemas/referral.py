# app/schemas/referral.py
# 2026-11 推薦好友（跟 mynotification plan-referral.md 對過的契約）
from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.schemas.coupon import CouponOut


class ReferredByOut(BaseModel):
    display_name: str  # 暱稱優先，沒有就遮罩 email（st***@hotmail.com）


class ReferralMeOut(BaseModel):
    code: str
    successful_count: int  # 已完成首購的推薦人數
    pending_count: int  # 已填碼、還沒首購的人數
    rewarded_this_year: int  # 今年（台灣時間）推薦人已領到的獎勵次數
    yearly_limit: int
    referred_by: Optional[ReferredByOut] = None
    can_apply_code: bool
    apply_deadline: Optional[datetime] = None  # 不能補填時為 null


class ReferralApplyRequest(BaseModel):
    code: str


class ReferralApplyOut(BaseModel):
    coupon: CouponOut  # 新會員的 $50 券，格式同 /coupons/me
    referred_by: ReferredByOut
