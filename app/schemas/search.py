# app/schemas/search.py
from pydantic import BaseModel


class SearchSuggestionOut(BaseModel):
    keyword: str

    class Config:
        from_attributes = True  # 允許從 SQLAlchemy 模型轉換
