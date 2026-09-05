# app/scripts/seed_products.py
"""
一次性／可重複執行的商品匯入腳本：把 DummyJSON 的商品資料匯入本地 products 表，
純粹作為購物車／金流功能開發測試用的展示資料，不是正式商品目錄。用 external_id
做 upsert（已存在就更新欄位，不存在才新增），重複執行不會產生重複資料。

用法：
    python -m app.scripts.seed_products
"""

import asyncio

import httpx
from sqlalchemy import select

from app.database_async import AsyncSessionLocal
from app.models import Product

# limit=0 代表回傳 DummyJSON 目前所有商品（不分頁）
DUMMYJSON_URL = "https://dummyjson.com/products?limit=0"


async def fetch_dummyjson_products() -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(DUMMYJSON_URL)
        response.raise_for_status()
        data = response.json()
    return data["products"]


async def seed_products() -> None:
    items = await fetch_dummyjson_products()
    print(f"從 DummyJSON 取得 {len(items)} 筆商品，開始匯入...")

    created = 0
    updated = 0

    async with AsyncSessionLocal() as db:
        for item in items:
            external_id = item["id"]
            query = select(Product).where(Product.external_id == external_id)
            result = await db.execute(query)
            product = result.scalars().first()

            fields = {
                "title": item["title"],
                "description": item["description"],
                "category": item["category"],
                "price": item["price"],
                "thumbnail": item["thumbnail"],
                "images": item.get("images", []),
                "stock": item.get("stock", 0),
            }

            if product:
                for key, value in fields.items():
                    setattr(product, key, value)
                updated += 1
            else:
                db.add(Product(external_id=external_id, is_active=True, **fields))
                created += 1

        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            print(f"DEBUG: 商品匯入失敗: {e}")
            raise

    print(f"匯入完成：新增 {created} 筆、更新 {updated} 筆。")


if __name__ == "__main__":
    asyncio.run(seed_products())
