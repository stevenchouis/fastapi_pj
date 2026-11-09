# app/schemas/store_checkout.py
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, condecimal

from app.schemas.coupon import CouponOut


class MemberCodeLookupRequest(BaseModel):
    member_code: str


class MemberCodeLookupOut(BaseModel):
    """
    店員掃碼／輸入會員碼後的「預覽」結果——只查不消費，讓店員在送出結帳前
    能看到能不能用點數/券折抵。真正標記會員碼已使用是送出結帳（POST /store-checkouts）
    當下才會發生。
    """

    user_id: int
    email: Optional[str] = None
    loyalty_balance: int
    coupons: List[CouponOut]
    # 2026-11 會員等級，讓店員看得到是不是金卡客人（純顯示用）
    tier: Literal["regular", "silver", "gold"] = "regular"
    einvoice_carrier: Optional[str] = None  # 2026-11 會員目前的電子發票載具，給店員 App 顯示


class StoreCheckoutCreate(BaseModel):
    member_code: str
    # 店員手動輸入的金額——這裡沒有商品/庫存可以當金額的權威來源，是刻意的信任層級
    subtotal: condecimal(gt=0, decimal_places=2)
    payment_method: Literal["cash", "jkopay"]
    coupon_id: Optional[int] = None
    # 規則同 /orders：1 點 = NT$1，單筆最高折抵「券後金額」50%
    use_points: int = 0


class StoreCheckoutOut(BaseModel):
    id: int
    user_id: int
    staff_user_id: int
    restaurant_id: int
    subtotal: float
    payment_method: str
    coupon_id: Optional[int] = None
    coupon_discount: float = 0
    points_used: int = 0
    points_discount: float = 0
    total_amount: float
    points_earned: int = 0
    einvoice_carrier: Optional[str] = None  # 2026-11 下單當下的電子發票載具快照
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
