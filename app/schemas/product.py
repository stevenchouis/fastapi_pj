# app/schemas/product.py
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, condecimal


class ProductOut(BaseModel):
    id: int
    title: str
    description: str
    category: str
    price: float
    thumbnail: str
    images: List[str]

    class Config:
        from_attributes = True  # 允許從 SQLAlchemy 模型轉換


class ProductAdminOut(ProductOut):
    """店員管理後台用，比 ProductOut 多回傳 stock/is_active 等內部欄位。"""

    stock: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProductCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str
    category: str = Field(min_length=1)
    price: condecimal(gt=0, decimal_places=2)
    thumbnail: str
    images: List[str] = []
    stock: int = Field(default=0, ge=0)
    is_active: bool = True


class ProductUpdate(BaseModel):
    """
    PATCH 用。price/is_active 等欄位是絕對覆蓋（last-write-wins）；
    stock 則是相對增減（店員輸入這次進貨/校正的數量差），用 stock_delta
    表示，後端以「UPDATE ... SET stock = stock + :delta WHERE stock + :delta >= 0」
    原子性套用，避免多店員同時補貨互相蓋掉，也避免扣成負庫存。
    """

    title: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    category: Optional[str] = Field(default=None, min_length=1)
    price: Optional[condecimal(gt=0, decimal_places=2)] = None
    thumbnail: Optional[str] = None
    images: Optional[List[str]] = None
    is_active: Optional[bool] = None
    stock_delta: Optional[int] = None
