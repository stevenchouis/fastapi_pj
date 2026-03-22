from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.core import security  # 假設你這裡有 create_access_token 邏輯
from app.core.config import settings
from app.database_async import get_db

# from app.main4 import create_access_token

router = APIRouter()


@router.post("/login/access-token")
async def login_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),  # 改用 AsyncSession 型別
):
    # 1. 使用 where 並確保 models 匯入正確
    statement = select(models.User).where(models.User.email == form_data.username)
    print(f"DEBUG DB TYPE: {type(db)}")
    # 2. 執行並捕捉結果
    result = await db.execute(statement)

    # 3. 使用 scalar_one_or_none 防止多筆或無資料時報錯
    # user = result.scalar_one_or_none()
    user = result.scalars().first()

    if not user or not security.verify_password(
        form_data.password, user.hashed_password
    ):
        raise HTTPException(status_code=400, detail="帳號或密碼錯誤")

    # 產生一個代表 30 分鐘長度的 timedelta 物件
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        # subject=user.email,  # 這裡對應函式的 subject 參數
        subject=str(user.id),  # 將 ID 轉為字串放入 sub
        expires_delta=access_token_expires,
    )
    # access_token = security.create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}


# @router.post("/login/access-token", response_model=schemas.Token)
# async def login_access_token(
#     db: AsyncSession = Depends(get_db),
#     form_data: OAuth2PasswordRequestForm = Depends(),  # 接收 username 和 password
# ) -> Any:
#     """
#     OAuth2 相容的登入方式，回傳 JWT Token
#     """
#     # # 1. 查找使用者
#     # result = await db.execute(
#     #     select(models.User).where(models.User.email == form_data.username)
#     # )
#     # user = result.scalars().first()
#     # 1. 建立查詢物件 (這只是一個描述，還沒執行)
#     query = select(models.User).where(models.User.email == form_data.username)

#     # 2. 使用 await 執行查詢
#     result = await db.execute(query)

#     # 3. 取得結果 (使用 scalars() 將結果轉為 User 物件流，再拿第一個)
#     user = result.scalars().first()

#     # 2. 驗證使用者與密碼 (假設你 models.User 裡有驗證邏輯)
#     if not user or not security.verify_password(
#         form_data.password, user.hashed_password
#     ):
#         raise HTTPException(
#             status_code=status.HTTP_400_BAD_REQUEST,
#             detail="電子郵件或密碼錯誤",
#         )

#     # 3. 產生 Token
#     access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
#     return {
#         "access_token": security.create_access_token(
#             user.id, expires_delta=access_token_expires
#         ),
#         "token_type": "bearer",
#     }
