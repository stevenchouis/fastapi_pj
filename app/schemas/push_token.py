from datetime import datetime
from typing import Optional

from pydantic import BaseModel


# 前端傳送過來的格式 (POST 請求體)
class PushTokenCreate(BaseModel):
    token: str
    device_name: Optional[str] = None


# API 回傳時的格式 (如果需要回傳列表)
class PushToken(BaseModel):
    id: int
    token: str
    device_name: Optional[str]
    user_id: int
    created_at: datetime

    class Config:
        from_attributes = True  # 讓 Pydantic 可以讀取 SQLAlchemy 物件
