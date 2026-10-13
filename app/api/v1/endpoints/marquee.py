# app/api/v1/endpoints/marquee.py
from datetime import UTC, datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import MarqueeItem
from app.schemas.marquee import (
    MarqueeItemAdminOut,
    MarqueeItemCreate,
    MarqueeItemOut,
    MarqueeItemUpdate,
)

router = APIRouter()


@router.get("", response_model=List[MarqueeItemOut])
async def list_marquee(db: AsyncSession = Depends(get_db)):
    """
    取得首頁跑馬燈文字，公開端點（不需登入）。只回傳啟用中（is_active）、且落在
    start_at/end_at 生效區間內的項目（比照 GET /promotions/home-banners 同一套
    查詢條件），依 sort_order 由小到大排序。
    """
    now = datetime.now(UTC)
    query = (
        select(MarqueeItem)
        .where(MarqueeItem.is_active.is_(True))
        .where(or_(MarqueeItem.start_at.is_(None), MarqueeItem.start_at <= now))
        .where(or_(MarqueeItem.end_at.is_(None), MarqueeItem.end_at >= now))
        .order_by(MarqueeItem.sort_order)
    )
    result = await db.execute(query)
    return result.scalars().all()


# 注意：/admin 要放在 /{marquee_id} 前面註冊，不然 "admin" 會先被
# /{marquee_id}（int）那條路由吃掉，變成 422 而不是進到這支（Product 已踩過的坑）。
@router.get(
    "/admin",
    response_model=List[MarqueeItemAdminOut],
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def list_marquee_admin(db: AsyncSession = Depends(get_db)):
    """
    後台（staff-scanner）跑馬燈管理列表，含未上架/不在生效區間內的項目。
    用 verify_admin_or_staff 雙軌驗證（X-Admin-Key 或 role="staff" JWT），
    比照 Restaurant CRUD——跑馬燈是全站共用的品牌/行銷內容，不分門市，
    性質上比較接近「改門市資訊」這種決策，不是 Product 那種日常營運操作，
    所以不採用純 role=staff（也不做任何門市 scope，本來就不分門市）。
    """
    query = select(MarqueeItem).order_by(MarqueeItem.sort_order)
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "",
    response_model=MarqueeItemAdminOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def create_marquee_item(
    payload: MarqueeItemCreate,
    db: AsyncSession = Depends(get_db),
):
    """新增跑馬燈文字。"""
    try:
        item = MarqueeItem(**payload.model_dump())
        db.add(item)
        await db.flush()
        item_id = item.id
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 新增跑馬燈文字失敗: {e}")
        raise HTTPException(status_code=500, detail="新增跑馬燈文字失敗")

    result = await db.execute(select(MarqueeItem).where(MarqueeItem.id == item_id))
    return result.scalars().first()


@router.patch(
    "/{marquee_id}",
    response_model=MarqueeItemAdminOut,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def update_marquee_item(
    marquee_id: int,
    payload: MarqueeItemUpdate,
    db: AsyncSession = Depends(get_db),
):
    """編輯跑馬燈文字，全欄位絕對覆蓋（last-write-wins）；上下架也是走這支（is_active）。"""
    result = await db.execute(select(MarqueeItem).where(MarqueeItem.id == marquee_id))
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="跑馬燈文字不存在")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 更新跑馬燈文字失敗: {e}")
        raise HTTPException(status_code=500, detail="更新跑馬燈文字失敗")

    result = await db.execute(select(MarqueeItem).where(MarqueeItem.id == marquee_id))
    return result.scalars().first()


@router.delete(
    "/{marquee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def delete_marquee_item(
    marquee_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    刪除跑馬燈文字——跟 Product/MenuItem 不同，這裡做真的硬刪除（不是軟刪除），
    因為沒有任何表會參照 MarqueeItem.id，沒有歷史資料完整性顧慮。暫時下架
    但保留資料的情境請用 PATCH {"is_active": false}。
    """
    result = await db.execute(select(MarqueeItem).where(MarqueeItem.id == marquee_id))
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="跑馬燈文字不存在")

    try:
        await db.delete(item)
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 刪除跑馬燈文字失敗: {e}")
        raise HTTPException(status_code=500, detail="刪除跑馬燈文字失敗")
