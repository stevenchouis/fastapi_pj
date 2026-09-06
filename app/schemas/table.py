# app/schemas/table.py
from datetime import datetime

from pydantic import BaseModel, Field


class TableCreate(BaseModel):
    code: str = Field(min_length=1)


class TableOut(BaseModel):
    id: int
    code: str
    created_at: datetime

    class Config:
        from_attributes = True  # 允許從 SQLAlchemy 模型轉換
