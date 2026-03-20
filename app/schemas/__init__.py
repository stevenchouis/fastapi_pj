# app/schemas/__init__.py
from .push_token import PushTokenCreate  # 之前定義過的 PushToken
from .token import Token, TokenData

# 如果有 user schema 也可以一併匯入
# from .user import User, UserCreate

# 明確定義匯出的內容，Linter 就不會再報錯
__all__ = [
    "Token",
    "TokenData",
    "PushToken",
    "PushTokenCreate",
]
