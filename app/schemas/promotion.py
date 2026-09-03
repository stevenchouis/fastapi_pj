# app/schemas/promotion.py
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
