# app/schemas/dine_in_order.py
from datetime import datetime
from typing import List, Literal

from pydantic import BaseModel, Field


class DineInOrderItemCreate(BaseModel):
    menu_item_id: int
    quantity: int = Field(gt=0)


class DineInOrderCreate(BaseModel):
    table_number: str = Field(min_length=1)
    items: List[DineInOrderItemCreate] = Field(min_length=1)


class DineInOrderItemOut(BaseModel):
    menu_item_id: int
    name: str
    quantity: int
    unit_price: float
    subtotal: float

    class Config:
        from_attributes = True


class DineInOrderOut(BaseModel):
    id: int
    table_number: str
    status: str
    total_amount: float
    created_at: datetime
    items: List[DineInOrderItemOut]

    class Config:
        from_attributes = True


class DineInOrderStatusUpdate(BaseModel):
    # 目前只開放標記完成；之後如果要支援更細的現場流程（備餐中等）再加合法值
    status: Literal["completed"]
