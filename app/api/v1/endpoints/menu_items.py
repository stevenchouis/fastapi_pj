# app/api/v1/endpoints/menu_items.py
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database_async import get_db
from app.models import MenuItem
from app.schemas.menu_item import MenuItemOut

router = APIRouter()


@router.get("", response_model=List[MenuItemOut])
async def list_menu_items(
    category: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    堂食點餐用的菜單清單，跟網購商店的 /products 是分開的資源。
    只回傳 is_available 的品項；沒有庫存概念，賣完由店員手動關閉 is_available。
    """
    query = select(MenuItem).where(MenuItem.is_available.is_(True))
    if category:
        query = query.where(MenuItem.category == category)
    query = query.order_by(MenuItem.id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{menu_item_id}", response_model=MenuItemOut)
async def get_menu_item(menu_item_id: int, db: AsyncSession = Depends(get_db)):
    query = select(MenuItem).where(
        MenuItem.id == menu_item_id, MenuItem.is_available.is_(True)
    )
    result = await db.execute(query)
    menu_item = result.scalars().first()
    if not menu_item:
        raise HTTPException(status_code=404, detail="品項不存在")
    return menu_item
