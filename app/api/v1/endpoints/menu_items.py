# app/api/v1/endpoints/menu_items.py
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import MenuItem
from app.schemas.menu_item import (
    MenuItemAdminOut,
    MenuItemCreate,
    MenuItemOut,
    MenuItemUpdate,
)

router = APIRouter()


@router.get("", response_model=List[MenuItemOut])
async def list_menu_items(
    category: Optional[str] = Query(default=None),
    restaurant_id: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    堂食點餐用的菜單清單，跟網購商店的 /products 是分開的資源。
    只回傳 is_available 的品項；沒有庫存概念，賣完由店員手動關閉 is_available。
    `restaurant_id` 是 2026-09 多門市支援新增的可選篩選（菜單每間門市各自獨立）——
    保持可選是為了向下相容還沒更新到「選餐廳」流程的舊版前端，不帶就回傳全部門市
    的品項（舊行為）。
    """
    query = select(MenuItem).where(MenuItem.is_available.is_(True))
    if category:
        query = query.where(MenuItem.category == category)
    if restaurant_id is not None:
        query = query.where(MenuItem.restaurant_id == restaurant_id)
    query = query.order_by(MenuItem.id)
    result = await db.execute(query)
    return result.scalars().all()


# 注意：/admin 要放在 /{menu_item_id} 前面註冊，不然 "admin" 會先被
# /{menu_item_id}（int）那條路由吃掉，變成 422 而不是進到這支。
@router.get("/admin", response_model=List[MenuItemAdminOut])
async def list_menu_items_admin(
    category: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """
    店員後台菜單列表，含已下架（is_available=False）品項，供產品資料維護功能使用。
    自動依登入店員的 User.restaurant_id 篩選，不接受前端傳門市參數（見 tables.py
    同樣的多門市 scoping 慣例）。
    """
    if current_user.restaurant_id is None:
        raise HTTPException(status_code=400, detail="帳號尚未指定所屬門市")
    query = select(MenuItem).where(MenuItem.restaurant_id == current_user.restaurant_id)
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


@router.post("", response_model=MenuItemAdminOut, status_code=status.HTTP_201_CREATED)
async def create_menu_item(
    payload: MenuItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """店員新增菜單品項，自動掛到登入店員所屬的門市。"""
    if current_user.restaurant_id is None:
        raise HTTPException(status_code=400, detail="帳號尚未指定所屬門市")
    try:
        menu_item = MenuItem(
            **payload.model_dump(), restaurant_id=current_user.restaurant_id
        )
        db.add(menu_item)
        await db.flush()
        menu_item_id = menu_item.id
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 新增菜單品項失敗: {e}")
        raise HTTPException(status_code=500, detail="新增菜單品項失敗")

    result = await db.execute(select(MenuItem).where(MenuItem.id == menu_item_id))
    return result.scalars().first()


@router.patch("/{menu_item_id}", response_model=MenuItemAdminOut)
async def update_menu_item(
    menu_item_id: int,
    payload: MenuItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """
    店員編輯菜單品項。全部欄位皆為絕對覆蓋（last-write-wins）——MenuItem 沒有庫存
    數量概念，刪除（下架）用 PATCH {"is_available": false} 即可，不提供 DELETE。
    只能編輯自己門市的品項，不屬於自己門市（或不存在）一律回 404，避免洩漏其他
    門市的品項 id 是否存在。
    """
    if current_user.restaurant_id is None:
        raise HTTPException(status_code=400, detail="帳號尚未指定所屬門市")
    result = await db.execute(
        select(MenuItem).where(
            MenuItem.id == menu_item_id,
            MenuItem.restaurant_id == current_user.restaurant_id,
        )
    )
    menu_item = result.scalars().first()
    if not menu_item:
        raise HTTPException(status_code=404, detail="品項不存在")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(menu_item, field, value)

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 更新菜單品項失敗: {e}")
        raise HTTPException(status_code=500, detail="更新菜單品項失敗")

    result = await db.execute(select(MenuItem).where(MenuItem.id == menu_item_id))
    return result.scalars().first()
