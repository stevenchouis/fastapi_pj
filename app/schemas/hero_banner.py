# app/schemas/hero_banner.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

# 點擊導向的白名單——三方（後端／mynotification／staff-scanner）寫死同一份，
# 任一邊要加新路由都要三邊同步更新，不是後端單方面能決定的清單
HERO_BANNER_LINK_ROUTES = frozenset(
    {
        "/coupons",
        "/points",
        "/favorites",
        "/inbox",
        "/member-code",
        "/shop",
        "/search",
        "/settings",
    }
)


def _validate_link_route(value: Optional[str]) -> Optional[str]:
    if value is not None and value not in HERO_BANNER_LINK_ROUTES:
        raise ValueError(f"link_route 必須是 null 或白名單內的路由：{sorted(HERO_BANNER_LINK_ROUTES)}")
    return value


class HeroBannerOut(BaseModel):
    id: int
    title: str
    subtitle: str
    color: str
    image_url: Optional[str] = None
    link_route: Optional[str] = None

    class Config:
        from_attributes = True


class HeroBannerAdminOut(HeroBannerOut):
    sort_order: int
    is_active: bool
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class HeroBannerCreate(BaseModel):
    title: str
    subtitle: str
    color: str
    image_url: Optional[str] = None
    link_route: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None

    _validate_link_route = field_validator("link_route")(_validate_link_route)


class HeroBannerUpdate(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    color: Optional[str] = None
    image_url: Optional[str] = None
    link_route: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None

    _validate_link_route = field_validator("link_route")(_validate_link_route)
