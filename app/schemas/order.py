# app/schemas/order.py
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class OrderCreate(BaseModel):
    items: List[OrderItemCreate] = Field(min_length=1)
    # 要折抵的點數（可選）。1 點 = NT$1，單筆訂單最高可折抵訂單金額 50%，
    # 後端會依商品小計重新驗證上限，不採信前端算好的折抵金額
    use_points: int = Field(default=0, ge=0)


class OrderItemOut(BaseModel):
    product_id: int
    title: str
    quantity: int
    unit_price: float
    subtotal: float

    class Config:
        from_attributes = True


class OrderOut(BaseModel):
    id: int
    status: str
    total_amount: float
    points_used: int = 0
    points_discount: float = 0
    payment_provider: str
    merchant_trade_no: str
    created_at: datetime
    paid_at: Optional[datetime] = None
    items: List[OrderItemOut]

    class Config:
        from_attributes = True
