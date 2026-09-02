from .google_auth import GoogleLoginRequest
from .magic_link import MagicLinkRequest, MagicLinkVerify
from .notification import NotificationLog, NotificationLogBase  # 新增的通知 Schema
from .push_token import PushToken, PushTokenCreate
from .token import Token, TokenData

# from .user import User, UserCreate, UserUpdate  # 確保 User 有被導出
from .user import User, UserCreate  # 確保 User 有被導出

# 透過 __all__ 明確告知 Ruff 這些匯入是為了提供給外部使用
# 這樣 Ruff 就不會將其視為「未使用的匯入」而刪除
__all__ = [
    "Token",
    "TokenData",
    "PushToken",
    "PushTokenCreate",
    "NotificationLog",
    "NotificationLogBase",
    "User",
    "UserCreate",
    "UserUpdate",
    "GoogleLoginRequest",
    "MagicLinkRequest",
    "MagicLinkVerify",
]
