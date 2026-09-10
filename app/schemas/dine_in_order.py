# app/schemas/dine_in_order.py
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class DineInOrderItemCreate(BaseModel):
    menu_item_id: int
    quantity: int = Field(gt=0)


class DineInOrderCreate(BaseModel):
    """
    2026-09 多門市支援：新版前端會帶 table_id（顧客從清單選的桌位，後端據此反查
    門市），舊版前端可能還是只送 table_number（自由文字，沒有門市概念）——兩者
    至少要帶一個，table_id 存在時以它為準（table_number 會被忽略，回應時改用
    查到的 table.code）。
    """

    table_number: Optional[str] = Field(default=None, min_length=1)
    table_id: Optional[int] = None
    items: List[DineInOrderItemCreate] = Field(min_length=1)
    # 要折抵的點數（可選），規則同 /orders：1 點 = NT$1，單筆最高折抵訂單金額 50%
    use_points: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _require_table_identifier(self):
        if self.table_id is None and not self.table_number:
            raise ValueError("必須提供 table_id 或 table_number 其中一個")
        return self


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
