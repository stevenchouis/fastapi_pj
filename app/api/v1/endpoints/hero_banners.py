# app/api/v1/endpoints/hero_banners.py
from datetime import UTC, datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import HeroBanner
from app.schemas.hero_banner import (
    HeroBannerAdminOut,
    HeroBannerCreate,
    HeroBannerOut,
    HeroBannerUpdate,
)

router = APIRouter()


@router.get("", response_model=List[HeroBannerOut])
async def list_hero_banners(db: AsyncSession = Depends(get_db)):
    """
    取得首頁大輪播內容，公開端點（不需登入），取代前端原本寫死的 BANNER_COPY。
    只回傳啟用中（is_active）、且落在 start_at/end_at 生效區間內的項目（跟
    GET /promotions/home-banners 同一套查詢條件），依 sort_order 由小到大排序。
    """
    now = datetime.now(UTC)
    query = (
        select(HeroBanner)
        .where(HeroBanner.is_active.is_(True))
        .where(or_(HeroBanner.start_at.is_(None), HeroBanner.start_at <= now))
        .where(or_(HeroBanner.end_at.is_(None), HeroBanner.end_at >= now))
        .order_by(HeroBanner.sort_order)
    )
    result = await db.execute(query)
    return result.scalars().all()


# 注意：/admin 要放在 /{hero_banner_id} 前面註冊，不然 "admin" 會先被
# /{hero_banner_id}（int）那條路由吃掉，變成 422 而不是進到這支（Product 已踩過的坑）。
@router.get(
    "/admin",
    response_model=List[HeroBannerAdminOut],
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def list_hero_banners_admin(db: AsyncSession = Depends(get_db)):
    """
    後台（staff-scanner）大輪播管理列表，含未上架/不在生效區間內的項目。
    用 verify_admin_or_staff 雙軌驗證（X-Admin-Key 或 role="staff" JWT），
    比照 MarqueeItem/Promotion——大輪播是全站共用的品牌/行銷內容，不分門市。
    """
    query = select(HeroBanner).order_by(HeroBanner.sort_order)
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "",
    response_model=HeroBannerAdminOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def create_hero_banner(
    payload: HeroBannerCreate,
    db: AsyncSession = Depends(get_db),
):
    """新增大輪播項目。"""
    try:
        item = HeroBanner(**payload.model_dump())
        db.add(item)
        await db.flush()
        item_id = item.id
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 新增大輪播失敗: {e}")
        raise HTTPException(status_code=500, detail="新增大輪播失敗")

    result = await db.execute(select(HeroBanner).where(HeroBanner.id == item_id))
    return result.scalars().first()


@router.patch(
    "/{hero_banner_id}",
    response_model=HeroBannerAdminOut,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def update_hero_banner(
    hero_banner_id: int,
    payload: HeroBannerUpdate,
    db: AsyncSession = Depends(get_db),
):
    """編輯大輪播項目，全欄位絕對覆蓋（last-write-wins）；上下架也走這支（is_active）。"""
    result = await db.execute(select(HeroBanner).where(HeroBanner.id == hero_banner_id))
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="大輪播項目不存在")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 更新大輪播失敗: {e}")
        raise HTTPException(status_code=500, detail="更新大輪播失敗")

    result = await db.execute(select(HeroBanner).where(HeroBanner.id == hero_banner_id))
    return result.scalars().first()


@router.delete(
    "/{hero_banner_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def delete_hero_banner(
    hero_banner_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    刪除大輪播項目——跟 Product/MenuItem 不同，這裡做真的硬刪除（不是軟刪除），
    因為沒有任何表會參照 HeroBanner.id，沒有歷史資料完整性顧慮。暫時下架但保留
    資料的情境請用 PATCH {"is_active": false}。
    """
    result = await db.execute(select(HeroBanner).where(HeroBanner.id == hero_banner_id))
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="大輪播項目不存在")

    try:
        await db.delete(item)
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 刪除大輪播失敗: {e}")
        raise HTTPException(status_code=500, detail="刪除大輪播失敗")
