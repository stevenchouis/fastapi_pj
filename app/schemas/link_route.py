# app/schemas/link_route.py
"""
首頁大輪播（HeroBanner）與開屏彈窗（PopupBanner）共用的點擊導向白名單。

三方（後端／mynotification／staff-scanner）寫死同一份清單，任一邊要加新路由都要
三邊同步更新，不是後端單方面能決定的清單。刻意不做成 DB 層 CHECK constraint，
因為清單之後可能變動，改 Python 常數比改 migration 簡單。

2026-11-09 從 8 個加到 10 個（新增 /referral、/points-exchange，使用者已確認）。
"""
from typing import Optional

BANNER_LINK_ROUTES = frozenset(
    {
        "/coupons",
        "/points",
        "/favorites",
        "/inbox",
        "/member-code",
        "/shop",
        "/search",
        "/settings",
        "/referral",
        "/points-exchange",
    }
)


def validate_link_route(value: Optional[str]) -> Optional[str]:
    """Pydantic field_validator 用：null 或白名單內的路由才行，否則 422。"""
    if value is not None and value not in BANNER_LINK_ROUTES:
        raise ValueError(f"link_route 必須是 null 或白名單內的路由：{sorted(BANNER_LINK_ROUTES)}")
    return value
