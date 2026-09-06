# app/api/deps.py
import secrets

from fastapi import Depends, Header, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.database_async import get_db
from app.models import User

# 這裡的 tokenUrl 必須對應到你實作「登入邏輯」的那個 API 路徑
reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token"
)
# auto_error=False：給 verify_admin_or_staff 用，沒帶 Authorization header 時不要直接
# 401，讓它有機會改用 X-Admin-Key 那條路徑
optional_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token", auto_error=False
)


async def get_current_user(
    db: AsyncSession = Depends(get_db), token: str = Depends(reusable_oauth2)
) -> User:
    try:
        # 解碼並驗證 JWT
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Token 無效")
    except JWTError:
        raise HTTPException(status_code=401, detail="無法驗證憑證")

    # 查資料庫
    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalars().first()

    if not user:
        raise HTTPException(status_code=404, detail="找不到使用者")

    return user


async def verify_admin_key(x_admin_key: str = Header(...)) -> None:
    """管理者專用端點的簡易保護：比對固定密鑰。用 constant-time 比較避免 timing attack。"""
    if not secrets.compare_digest(x_admin_key, settings.ADMIN_API_KEY):
        raise HTTPException(status_code=401, detail="Admin Key 無效")


async def get_current_staff_user(current_user: User = Depends(get_current_user)) -> User:
    """跟 get_current_user 一樣先驗證 JWT，再多檢查 role 是不是 staff。"""
    if current_user.role != "staff":
        raise HTTPException(status_code=403, detail="需要店員權限")
    return current_user


async def verify_admin_or_staff(
    x_admin_key: str | None = Header(default=None),
    token: str | None = Depends(optional_oauth2),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    雙軌驗證，給 coupons/admin/issue 用：X-Admin-Key（老闆／營運用 Postman 手動發券）
    或 role=staff 的 JWT（店員 App 登入後用自己帳號發券）兩種方式皆可通過。
    """
    if x_admin_key is not None and secrets.compare_digest(
        x_admin_key, settings.ADMIN_API_KEY
    ):
        return

    if token:
        try:
            payload = jwt.decode(
                token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
            )
            user_id = payload.get("sub")
            if user_id is not None:
                result = await db.execute(select(User).where(User.id == int(user_id)))
                user = result.scalars().first()
                if user is not None and user.role == "staff":
                    return
        except JWTError:
            pass

    raise HTTPException(status_code=401, detail="需要 Admin Key 或店員權限")
