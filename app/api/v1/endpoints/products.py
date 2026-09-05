# app/api/v1/endpoints/products.py
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database_async import get_db
from app.models import Product
from app.schemas.product import ProductOut

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
