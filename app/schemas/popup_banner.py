# app/schemas/popup_banner.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator

from app.schemas.link_route import validate_link_route as _validate_link_route


def _required_text(value: Optional[str]) -> str:
    # title／image_url 必填：建立時不能是空字串；PATCH 時明確帶 null 或空字串也擋（422），
    # 不然 DB 的 NOT NULL 會讓它變成 500
    if value is None or not value.strip():
        raise ValueError("不可為空")
    return value.strip()


def _not_null(value):
    if value is None:
        raise ValueError("不可為 null")
    return value


class PopupBannerOut(BaseModel):
    id: int
    title: str
    image_url: str
    link_route: Optional[str] = None

    class Config:
        from_attributes = True


class PopupBannerAdminOut(PopupBannerOut):
    sort_order: int
    is_active: bool
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PopupBannerCreate(BaseModel):
    title: str
    image_url: str
    link_route: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None

    _validate_link_route = field_validator("link_route")(_validate_link_route)
    _validate_required = field_validator("title", "image_url")(_required_text)


class PopupBannerUpdate(BaseModel):
    """全欄位絕對覆蓋；沒帶的欄位不動。link_route／start_at／end_at 可以帶 null 清除。"""

    title: Optional[str] = None
    image_url: Optional[str] = None
    link_route: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None

    _validate_link_route = field_validator("link_route")(_validate_link_route)
    _validate_required = field_validator("title", "image_url")(_required_text)
    _validate_not_null = field_validator("sort_order", "is_active")(_not_null)
