# app/scripts/seed_hero_banners.py
"""
把 mynotification 前端原本寫死在 BANNER_COPY 的 4 筆大輪播內容搬進資料庫，
文字照原文、link_route 全部留空（null）、image_url 先留空，等 staff-scanner
用 Supabase Storage 上傳後再補上。用 title 做 upsert（已存在就更新欄位，
不存在才新增）。

用法：
    python -m app.scripts.seed_hero_banners
"""

import asyncio

from sqlalchemy import select

from app.database_async import AsyncSessionLocal
from app.models import HeroBanner

HERO_BANNERS = [
    {"title": "春季優惠開跑", "subtitle": "即日起至月底，全館 9 折", "color": "#EDE7DD", "sort_order": 0},
    {"title": "新品上架", "subtitle": "本週精選新品搶先看", "color": "#DCE3D5", "sort_order": 1},
    {"title": "會員專屬活動", "subtitle": "加入會員享更多回饋", "color": "#E3D9CE", "sort_order": 2},
    {"title": "簡約生活提案", "subtitle": "為日常增添一些留白", "color": "#D9D2C7", "sort_order": 3},
]


async def seed_hero_banners() -> None:
    created = 0
    updated = 0

    async with AsyncSessionLocal() as db:
        for fields in HERO_BANNERS:
            query = select(HeroBanner).where(HeroBanner.title == fields["title"])
            result = await db.execute(query)
            banner = result.scalars().first()

            if banner:
                for key, value in fields.items():
                    setattr(banner, key, value)
                updated += 1
            else:
                db.add(HeroBanner(is_active=True, **fields))
                created += 1

        try:
            await db.commit()
        except Exception as e:
            await db.rollback()
            print(f"DEBUG: 大輪播種子資料匯入失敗: {e}")
            raise

    print(f"匯入完成：新增 {created} 筆、更新 {updated} 筆。")


if __name__ == "__main__":
    asyncio.run(seed_hero_banners())
