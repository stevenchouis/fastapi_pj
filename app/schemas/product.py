# app/schemas/product.py
from typing import List

from pydantic import BaseModel


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
