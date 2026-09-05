# app/api/v1/endpoints/favorites.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import Favorite, Product
from app.schemas.favorite import FavoriteCreate
from app.schemas.product import ProductOut

router = APIRouter()


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def add_favorite(
    payload: FavoriteCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    加入收藏。已收藏過同一個商品直接視為成功（idempotent），前端不用另外判斷
    「是否已收藏」的情境。
    """
    product_result = await db.execute(
        select(Product.id).where(
            Product.id == payload.product_id, Product.is_active.is_(True)
        )
    )
    if product_result.first() is None:
        raise HTTPException(status_code=404, detail="商品不存在")

    existing_result = await db.execute(
        select(Favorite.id).where(
            Favorite.user_id == current_user.id,
            Favorite.product_id == payload.product_id,
        )
    )
    if existing_result.first() is not None:
        return

    try:
        db.add(Favorite(user_id=current_user.id, product_id=payload.product_id))
        await db.commit()
    except IntegrityError:
        # 同時點兩下等競態情況下撞到 unique 限制，視為已經收藏成功
        await db.rollback()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 加入收藏失敗: {e}")
        raise HTTPException(status_code=500, detail="加入收藏失敗")


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """取消收藏。本來就沒收藏過也回 204（idempotent）。"""
    statement = delete(Favorite).where(
        Favorite.user_id == current_user.id, Favorite.product_id == product_id
    )
    try:
        await db.execute(statement)
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 取消收藏失敗: {e}")
        raise HTTPException(status_code=500, detail="取消收藏失敗")


@router.get("/me", response_model=List[ProductOut])
async def get_my_favorites(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    取得目前登入使用者收藏的商品列表，直接回傳完整商品資料（跟 GET /products
    同樣的欄位），前端不用再逐一查詢商品明細。新收藏排在前面。
    """
    query = (
        select(Product)
        .join(Favorite, Favorite.product_id == Product.id)
        .where(Favorite.user_id == current_user.id, Product.is_active.is_(True))
        .order_by(Favorite.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()
