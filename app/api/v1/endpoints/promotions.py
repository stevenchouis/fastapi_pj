# app/api/v1/endpoints/promotions.py
from datetime import UTC, datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import Promotion
from app.schemas.promotion import (
    PromotionAdminOut,
    PromotionCreate,
    PromotionOut,
    PromotionUpdate,
)

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


@router.get(
    "/admin",
    response_model=List[PromotionAdminOut],
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def list_promotions_admin(db: AsyncSession = Depends(get_db)):
    """
    後台（staff-scanner）活動輪播管理列表，含未上架/不在生效區間內的項目。
    用 verify_admin_or_staff 雙軌驗證（X-Admin-Key 或 role="staff" JWT），比照
    MarqueeItem——首頁輪播是全站共用的品牌/行銷內容，不分門市，性質上比較接近
    「改門市資訊」這種決策，不是 Product 那種日常營運操作。
    """
    query = select(Promotion).order_by(Promotion.sort_order)
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "",
    response_model=PromotionAdminOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def create_promotion(
    payload: PromotionCreate,
    db: AsyncSession = Depends(get_db),
):
    """新增活動輪播項目。"""
    try:
        item = Promotion(**payload.model_dump())
        db.add(item)
        await db.flush()
        item_id = item.id
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 新增活動輪播失敗: {e}")
        raise HTTPException(status_code=500, detail="新增活動輪播失敗")

    result = await db.execute(select(Promotion).where(Promotion.id == item_id))
    return result.scalars().first()


@router.patch(
    "/{promotion_id}",
    response_model=PromotionAdminOut,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def update_promotion(
    promotion_id: int,
    payload: PromotionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """編輯活動輪播項目，全欄位絕對覆蓋（last-write-wins）；上下架也走這支（is_active）。"""
    result = await db.execute(select(Promotion).where(Promotion.id == promotion_id))
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="活動輪播項目不存在")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 更新活動輪播失敗: {e}")
        raise HTTPException(status_code=500, detail="更新活動輪播失敗")

    result = await db.execute(select(Promotion).where(Promotion.id == promotion_id))
    return result.scalars().first()


@router.delete(
    "/{promotion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def delete_promotion(
    promotion_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    刪除活動輪播項目——跟 Product/MenuItem 不同，這裡做真的硬刪除（不是軟刪除），
    因為沒有任何表會參照 Promotion.id，沒有歷史資料完整性顧慮。暫時下架但保留
    資料的情境請用 PATCH {"is_active": false}。
    """
    result = await db.execute(select(Promotion).where(Promotion.id == promotion_id))
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="活動輪播項目不存在")

    try:
        await db.delete(item)
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 刪除活動輪播失敗: {e}")
        raise HTTPException(status_code=500, detail="刪除活動輪播失敗")
