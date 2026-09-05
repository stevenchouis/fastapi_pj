# app/schemas/order.py
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class OrderItemCreate(BaseModel):
    product_id: int
    quantity: int = Field(gt=0)


class OrderCreate(BaseModel):
    items: List[OrderItemCreate] = Field(min_length=1)


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
    payment_provider: str
    merchant_trade_no: str
    created_at: datetime
    paid_at: Optional[datetime] = None
    items: List[OrderItemOut]

    class Config:
        from_attributes = True
