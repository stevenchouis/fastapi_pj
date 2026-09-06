# app/scripts/seed_menu_items.py
"""
一次性／可重複執行的菜單測試資料腳本，純粹供堂食點餐功能開發測試用，
不是正式菜單內容。用 name 做 upsert（已存在就更新欄位，不存在才新增）。

用法：
    python -m app.scripts.seed_menu_items
"""

import asyncio

from sqlalchemy import select

from app.database_async import AsyncSessionLocal
from app.models import MenuItem

TEST_MENU_ITEMS = [
    {
        "name": "招牌牛肉麵",
        "description": "紅燒牛腩＋牛筋，附酸菜",
        "category": "麵食",
        "price": 180,
        "image_url": "https://placehold.co/400x300?text=Beef+Noodle",
    },
    {
        "name": "滷肉飯",
        "description": "傳統爌肉滷汁淋飯",
        "category": "飯類",
        "price": 60,
        "image_url": "https://placehold.co/400x300?text=Braised+Pork+Rice",
    },
    {
        "name": "涼拌小黃瓜",
        "description": "蒜香開胃小菜",
        "category": "小菜",
        "price": 40,
        "image_url": "https://placehold.co/400x300?text=Cucumber+Salad",
    },
    {
        "name": "珍珠奶茶",
        "description": "冰／熱可選，附珍珠",
        "category": "飲料",
        "price": 50,
        "image_url": "https://placehold.co/400x300?text=Bubble+Tea",
    },
]


async def seed_menu_items() -> None:
    created = 0
    updated = 0

    async with AsyncSessionLocal() as db:
        for fields in TEST_MENU_ITEMS:
            query = select(MenuItem).where(MenuItem.name == fields["name"])
            result = await db.execute(query)
            menu_item = result.scalars().first()

            if menu_item:
                for key, value in fields.items():
                    setattr(menu_item, key, value)
                updated += 1
            else:
                db.add(MenuItem(is_available=True, **fields))
                created += 1

        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            print(f"DEBUG: 菜單測試資料匯入失敗: {e}")
            raise

    print(f"匯入完成：新增 {created} 筆、更新 {updated} 筆。")


if __name__ == "__main__":
    asyncio.run(seed_menu_items())
