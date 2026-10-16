# app/schemas/promotion.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PromotionOut(BaseModel):
    id: int
    tag: str
    title: str
    subtitle: str
    color: str
    image_url: str

    class Config:
        from_attributes = True  # 允許從 SQLAlchemy 模型轉換


class PromotionAdminOut(PromotionOut):
    sort_order: int
    is_active: bool
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PromotionCreate(BaseModel):
    tag: str
    title: str
    subtitle: str
    color: str
    image_url: str
    sort_order: int = 0
    is_active: bool = True
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None


class PromotionUpdate(BaseModel):
    tag: Optional[str] = None
    title: Optional[str] = None
    subtitle: Optional[str] = None
    color: Optional[str] = None
    image_url: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
