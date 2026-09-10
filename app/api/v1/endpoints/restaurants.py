# app/api/v1/endpoints/restaurants.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import Restaurant
from app.schemas.restaurant import RestaurantCreate, RestaurantOut

router = APIRouter()


@router.get("", response_model=List[RestaurantOut])
async def list_restaurants(db: AsyncSession = Depends(get_db)):
    """
    門市清單，公開端點（不需 JWT）——顧客端「選餐廳」畫面用，跟 GET /menu-items
    一樣不要求登入才能瀏覽。依名稱排序。
    """
    query = select(Restaurant).order_by(Restaurant.name)
    result = await db.execute(query)
    return result.scalars().all()


@router.post(
    "",
    response_model=RestaurantOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def create_restaurant(
    payload: RestaurantCreate,
    db: AsyncSession = Depends(get_db),
):
    """
    新增門市。用 verify_admin_or_staff 雙軌驗證（X-Admin-Key 或 role="staff"
    JWT 皆可）——開分店比日常桌位管理更接近店長/老闆層級的決策，但系統目前沒有
    比 staff 更細的管理者角色，先跟現有的 coupons/admin/issue 用同一種雙軌模式，
    之後如果需要更嚴格的權限（例如只允許 X-Admin-Key）再收緊。
    """
    try:
        restaurant = Restaurant(name=payload.name)
        db.add(restaurant)
        await db.flush()
        restaurant_id = restaurant.id
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 新增門市失敗: {e}")
        raise HTTPException(status_code=500, detail="新增門市失敗")

    result = await db.execute(select(Restaurant).where(Restaurant.id == restaurant_id))
    return result.scalars().first()


@router.delete(
    "/{restaurant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(deps.verify_admin_or_staff)],
)
async def delete_restaurant(
    restaurant_id: int,
    db: AsyncSession = Depends(get_db),
):
    """刪除門市，找不到回 404。"""
    result = await db.execute(select(Restaurant.id).where(Restaurant.id == restaurant_id))
    if result.first() is None:
        raise HTTPException(status_code=404, detail="門市不存在")

    try:
        await db.execute(delete(Restaurant).where(Restaurant.id == restaurant_id))
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 刪除門市失敗: {e}")
        raise HTTPException(status_code=500, detail="刪除門市失敗")
