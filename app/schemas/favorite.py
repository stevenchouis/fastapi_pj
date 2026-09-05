# app/schemas/favorite.py
from pydantic import BaseModel


class FavoriteCreate(BaseModel):
    product_id: int
