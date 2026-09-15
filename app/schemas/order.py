# app/schemas/order.py
from datetime import datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class OrderCreate(BaseModel):
    items: List[OrderItemCreate] = Field(min_length=1)
    # 要折抵的優惠券（可選）。後端驗證這張券屬於此使用者、未使用、未過期後直接
    # 折抵生效，跟到店核銷（產生核銷碼給店員掃）是不同通路，兩者不會雙重折抵
    # （共用同一個 is_used 欄位的原子性更新）。折抵順序：先套用券折扣（clamp 到
    # 不超過商品小計），再用「券後金額」計算 use_points 的折抵上限
    coupon_id: Optional[int] = None
    # 要折抵的點數（可選）。1 點 = NT$1，單筆訂單最高可折抵「券後金額」50%，
    # 後端會重新驗證上限，不採信前端算好的折抵金額
    use_points: int = Field(default=0, ge=0)


class OrderItemOut(BaseModel):
    product_id: int
    title: str
    quantity: int
    unit_price: float
    subtotal: float

    class Config:
        from_attributes = True


class OrderCheckoutOut(BaseModel):
    # 前端把 fields 組成表單（或 WebView 用的 auto-submit HTML）POST 到 action_url
    action_url: str
    fields: Dict[str, str]


class OrderStatusUpdate(BaseModel):
    # 目前只開放標成 shipped 這一個目標值，比照 DineInOrderStatusUpdate 的模式
    status: Literal["shipped"]


class OrderOut(BaseModel):
    id: int
    status: str
    total_amount: float
    coupon_id: Optional[int] = None
    coupon_discount: float = 0
    points_used: int = 0
    points_discount: float = 0
    points_earned: int = 0
    payment_provider: str
    merchant_trade_no: str
    created_at: datetime
    paid_at: Optional[datetime] = None
    items: List[OrderItemOut]

    class Config:
        from_attributes = True
