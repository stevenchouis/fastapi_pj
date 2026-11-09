# app/schemas/hero_banner.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

# 點擊導向白名單跟開屏彈窗共用，清單在 app/schemas/link_route.py
from app.schemas.link_route import validate_link_route as _validate_link_route


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
