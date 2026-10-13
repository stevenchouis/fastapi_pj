# app/schemas/marquee.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class MarqueeItemOut(BaseModel):
    text: str

    class Config:
        from_attributes = True


class MarqueeItemAdminOut(BaseModel):
    id: int
    text: str
    sort_order: int
    is_active: bool
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MarqueeItemCreate(BaseModel):
    text: str
    sort_order: int = 0
    is_active: bool = True
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None


class MarqueeItemUpdate(BaseModel):
    text: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
