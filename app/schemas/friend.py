# app/schemas/friend.py
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# username 是 email 前綴遮罩後的暫代顯示名稱（User 目前沒有暱稱欄位，見
# friends.py display_name），不回傳完整 email，避免陌生人透過邀請看到對方信箱。
# 所有回應都是「對方」的資料，欄位名稱跟 mynotification 前端對過（user_id/username）
class FriendOut(BaseModel):
    friendship_id: int
    user_id: int
    username: str
    avatar_url: Optional[str] = None
    since: Optional[datetime] = None


class FriendRequestOut(BaseModel):
    id: int  # = friendship_id，accept/reject 用這個
    requester_id: int
    username: str
    avatar_url: Optional[str] = None
    created_at: Optional[datetime] = None


class FriendRequestCreate(BaseModel):
    target_user_id: int


class FriendshipOut(BaseModel):
    id: int
    status: str  # pending / accepted / declined
    requester_id: int
    user_id: int  # 對方（不是呼叫者自己）
    username: str
    avatar_url: Optional[str] = None


class GreetingCreate(BaseModel):
    # 前端固定選項字串（例如「謝謝！」），不驗證內容，只限制長度避免濫用
    message: str = Field(min_length=1, max_length=50)
