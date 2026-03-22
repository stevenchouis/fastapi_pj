from datetime import UTC, datetime, timedelta
from typing import Any

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

# 1. 設定密碼加密方式 (使用 bcrypt)
pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")

users_db = {
    "testuser": {"username": "testuser", "password": pwd_context.hash("123456")}
}


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """驗證明文密碼與資料庫中的雜湊值是否匹配"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """將明文密碼轉換為雜湊值 (註冊時使用)"""
    return pwd_context.hash(password)


def create_access_token(subject: str | Any, expires_delta: timedelta = None) -> str:
    """產生 JWT Access Token"""
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

    # payload 內容，'sub' 通常放 user_id
    to_encode = {"exp": expire, "sub": str(subject)}

    # 使用 SECRET_KEY 進行簽名
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt
