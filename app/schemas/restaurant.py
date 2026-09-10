# app/schemas/restaurant.py
from datetime import datetime

from pydantic import BaseModel, Field


class RestaurantCreate(BaseModel):
    name: str = Field(min_length=1)


class RestaurantOut(BaseModel):
    id: int
    name: str
    created_at: datetime

    class Config:
        from_attributes = True  # 允許從 SQLAlchemy 模型轉換
