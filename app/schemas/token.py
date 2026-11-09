# app/schemas/token.py
from typing import Optional

from pydantic import BaseModel


class Token(BaseModel):
    access_token: str
    token_type: str
    refresh_token: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    """POST /login/refresh、POST /logout 的 body"""

    refresh_token: str


class TokenData(BaseModel):
    user_id: Optional[int] = None
