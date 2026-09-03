# app/api/v1/endpoints/search.py
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database_async import get_db
from app.models import SearchSuggestion
from app.schemas.search import SearchSuggestionOut

router = APIRouter()


@router.get("/suggestions", response_model=List[SearchSuggestionOut])
async def get_search_suggestions(db: AsyncSession = Depends(get_db)):
    """
    取得熱門搜尋標籤，給前端搜尋頁在使用者尚未輸入關鍵字時顯示。
    只回傳啟用中（is_active）的標籤，依 sort_order 由小到大排序。
    """
    query = (
        select(SearchSuggestion)
        .where(SearchSuggestion.is_active.is_(True))
        .order_by(SearchSuggestion.sort_order)
    )
    result = await db.execute(query)
    return result.scalars().all()
