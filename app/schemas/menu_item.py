# app/schemas/menu_item.py
from pydantic import BaseModel


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
