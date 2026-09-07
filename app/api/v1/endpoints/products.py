# app/api/v1/endpoints/products.py
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import Product
from app.schemas.product import (
    ProductAdminOut,
    ProductCreate,
    ProductOut,
    ProductUpdate,
)

router = APIRouter()


@router.get("", response_model=List[ProductOut])
async def list_products(
    category: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    商品清單，供購物車頁面使用。只回傳上架中（is_active）的商品；
    price/stock 一律以這裡的資料為權威，前端不應自行快取後拿來下單。
    """
    query = select(Product).where(Product.is_active.is_(True))
    if category:
        query = query.where(Product.category == category)
    query = query.order_by(Product.id)
    result = await db.execute(query)
    return result.scalars().all()


# 注意：/admin 要放在 /{product_id} 前面註冊，不然 "admin" 會先被
# /{product_id}（int）那條路由吃掉，變成 422 而不是進到這支。
@router.get("/admin", response_model=List[ProductAdminOut])
async def list_products_admin(
    category: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """店員後台商品列表，含已下架商品與 stock 欄位，供產品資料維護功能使用。"""
    query = select(Product)
    if category:
        query = query.where(Product.category == category)
    query = query.order_by(Product.id)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{product_id}", response_model=ProductOut)
async def get_product(product_id: int, db: AsyncSession = Depends(get_db)):
    query = select(Product).where(
        Product.id == product_id, Product.is_active.is_(True)
    )
    result = await db.execute(query)
    product = result.scalars().first()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    return product


@router.post("", response_model=ProductAdminOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """店員新增商品。"""
    try:
        product = Product(**payload.model_dump())
        db.add(product)
        await db.flush()
        product_id = product.id
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 新增商品失敗: {e}")
        raise HTTPException(status_code=500, detail="新增商品失敗")

    result = await db.execute(select(Product).where(Product.id == product_id))
    return result.scalars().first()


@router.patch("/{product_id}", response_model=ProductAdminOut)
async def update_product(
    product_id: int,
    payload: ProductUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """
    店員編輯商品。price/is_active/title 等欄位為絕對覆蓋（last-write-wins）；
    stock_delta（如有帶）用原子性 UPDATE 套用相對增減，避免多店員同時補貨互相蓋掉，
    也避免扣成負庫存（此時回 409）。
    """
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalars().first()
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")

    data = payload.model_dump(exclude_unset=True)
    stock_delta = data.pop("stock_delta", None)

    for field, value in data.items():
        setattr(product, field, value)

    try:
        if stock_delta is not None:
            statement = (
                update(Product)
                .where(Product.id == product_id)
                .where(Product.stock + stock_delta >= 0)
                .values(stock=Product.stock + stock_delta)
            )
            stock_result = await db.execute(statement)
            if stock_result.rowcount == 0:
                await db.rollback()
                raise HTTPException(status_code=409, detail="庫存不足，無法扣減至負數")
        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 更新商品失敗: {e}")
        raise HTTPException(status_code=500, detail="更新商品失敗")

    result = await db.execute(select(Product).where(Product.id == product_id))
    return result.scalars().first()
