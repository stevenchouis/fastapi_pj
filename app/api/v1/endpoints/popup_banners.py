# app/api/v1/endpoints/popup_banners.py
"""2026-11-09 首頁開屏活動彈窗，結構比照 hero_banners.py，詳見 docs/popup-banner.md。"""
from datetime import UTC, datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import PopupBanner
from app.schemas.popup_banner import (
    PopupBannerAdminOut,
    PopupBannerCreate,
    PopupBannerOut,
    PopupBannerUpdate,
)

router = APIRouter()


@router.get("", response_model=List[PopupBannerOut])
async def list_popup_banners(db: AsyncSession = Depends(get_db)):
    """
    取得開屏彈窗，公開端點（不需登入）。只回傳啟用中（is_active）、且落在
    start_at/end_at 生效區間內的項目（跟 GET /hero-banners 同一套條件），依 sort_order 排序。
    """
    now = datetime.now(UTC)
    query = (
        select(PopupBanner)
        .where(PopupBanner.is_active.is_(True))
        .where(or_(PopupBanner.start_at.is_(None), PopupBanner.start_at <= now))
        .where(or_(PopupBanner.end_at.is_(None), PopupBanner.end_at >= now))
        .order_by(PopupBanner.sort_order, PopupBanner.id)
    )
    result = await db.execute(query)
    return result.scalars().all()


# 注意：/admin 要放在 /{popup_banner_id} 前面註冊，不然 "admin" 會先被 int 路由吃掉變 422
@router.get(
    "/admin",
    response_model=List[PopupBannerAdminOut],
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def list_popup_banners_admin(db: AsyncSession = Depends(get_db)):
    """
    後台（staff-scanner）彈窗管理列表，含未啟用/不在生效區間內的項目。
    用 verify_admin_or_staff 雙軌驗證（X-Admin-Key 或 role="staff" JWT），比照 HeroBanner。
    """
    query = select(PopupBanner).order_by(PopupBanner.sort_order, PopupBanner.id)
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "",
    response_model=PopupBannerAdminOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def create_popup_banner(
    payload: PopupBannerCreate,
    db: AsyncSession = Depends(get_db),
):
    """新增開屏彈窗。"""
    try:
        item = PopupBanner(**payload.model_dump())
        db.add(item)
        await db.flush()
        item_id = item.id
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 新增開屏彈窗失敗: {e}")
        raise HTTPException(status_code=500, detail="新增開屏彈窗失敗")

    result = await db.execute(select(PopupBanner).where(PopupBanner.id == item_id))
    return result.scalars().first()


@router.patch(
    "/{popup_banner_id}",
    response_model=PopupBannerAdminOut,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def update_popup_banner(
    popup_banner_id: int,
    payload: PopupBannerUpdate,
    db: AsyncSession = Depends(get_db),
):
    """編輯開屏彈窗，帶了的欄位絕對覆蓋（last-write-wins）；上下架也走這支（is_active）。"""
    result = await db.execute(select(PopupBanner).where(PopupBanner.id == popup_banner_id))
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="開屏彈窗不存在")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 更新開屏彈窗失敗: {e}")
        raise HTTPException(status_code=500, detail="更新開屏彈窗失敗")

    result = await db.execute(select(PopupBanner).where(PopupBanner.id == popup_banner_id))
    return result.scalars().first()


@router.delete(
    "/{popup_banner_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def delete_popup_banner(
    popup_banner_id: int,
    db: AsyncSession = Depends(get_db),
):
    """硬刪除（沒有任何表參照 PopupBanner.id）；暫時下架請用 PATCH {"is_active": false}。"""
    result = await db.execute(select(PopupBanner).where(PopupBanner.id == popup_banner_id))
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="開屏彈窗不存在")

    try:
        await db.delete(item)
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 刪除開屏彈窗失敗: {e}")
        raise HTTPException(status_code=500, detail="刪除開屏彈窗失敗")
