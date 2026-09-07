# app/schemas/menu_item.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, condecimal


class MenuItemOut(BaseModel):
    id: int
    name: str
    description: str
    category: str
    price: float
    image_url: str
    is_available: bool

    class Config:
        from_attributes = True  # 允許從 SQLAlchemy 模型轉換


class MenuItemAdminOut(MenuItemOut):
    """店員管理後台用，比 MenuItemOut 多回傳 created_at/updated_at。"""

    created_at: datetime
    updated_at: datetime


class MenuItemCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str
    category: str = Field(min_length=1)
    price: condecimal(gt=0, decimal_places=2)
    image_url: str
    is_available: bool = True


class MenuItemUpdate(BaseModel):
    """
    PATCH 用，全部欄位皆為絕對覆蓋（last-write-wins）——MenuItem 沒有庫存數量概念，
    賣完由店員把 is_available 關掉即可，不像 Product 需要 stock_delta 相對增減。
    """

    name: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    category: Optional[str] = Field(default=None, min_length=1)
    price: Optional[condecimal(gt=0, decimal_places=2)] = None
    image_url: Optional[str] = None
    is_available: Optional[bool] = None
