# app/api/v1/endpoints/promotions.py
from datetime import UTC, datetime
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database_async import get_db
from app.models import Promotion
from app.schemas.promotion import PromotionOut

router = APIRouter()


@router.get("/home-banners", response_model=List[PromotionOut])
async def get_home_banners(db: AsyncSession = Depends(get_db)):
    """
    取得首頁小型活動輪播內容。
    只回傳啟用中（is_active）、且落在 start_at/end_at 生效區間內的項目，依 sort_order 由小到大排序。
    """
    now = datetime.now(UTC)
    query = (
        select(Promotion)
        .where(Promotion.is_active.is_(True))
        .where(or_(Promotion.start_at.is_(None), Promotion.start_at <= now))
        .where(or_(Promotion.end_at.is_(None), Promotion.end_at >= now))
        .order_by(Promotion.sort_order)
    )
    result = await db.execute(query)
    return result.scalars().all()
