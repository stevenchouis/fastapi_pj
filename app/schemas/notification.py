# app/schemas/notification.py
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class NotificationLogBase(BaseModel):
    title: str
    body: str
    data: Optional[Any] = None


class NotificationLog(NotificationLogBase):
    id: int
    user_id: int
    is_read: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)  # 重要：允許 ORM 轉換
