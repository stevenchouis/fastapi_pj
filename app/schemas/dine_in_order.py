# app/schemas/dine_in_order.py
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class DineInOrderItemCreate(BaseModel):
    menu_item_id: int
    quantity: int = Field(gt=0)


class DineInOrderCreate(BaseModel):
    """
    2026-09 多門市支援 Phase 3：table_id 改成必填——Phase 1/2 期間曾經接受舊版
    自由文字 table_number（沒有門市概念），兩邊前端（mynotification／staff-scanner）
    都已確認改用「選桌位」流程並實機測過，才收緊這裡不再接受純 table_number。
    """

    table_id: int
    items: List[DineInOrderItemCreate] = Field(min_length=1)
    # 要折抵的點數（可選），規則同 /orders：1 點 = NT$1，單筆最高折抵訂單金額 50%
    use_points: int = Field(default=0, ge=0)


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
    table_id: Optional[int] = None
    restaurant_id: Optional[int] = None
    status: str
    total_amount: float
    points_used: int = 0
    points_discount: float = 0
    points_earned: int = 0
    created_at: datetime
    items: List[DineInOrderItemOut]

    class Config:
        from_attributes = True


class DineInOrderStatusUpdate(BaseModel):
    # 目前只開放標記完成；之後如果要支援更細的現場流程（備餐中等）再加合法值
    status: Literal["completed"]
