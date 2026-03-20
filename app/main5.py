from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models  # 假設你把上面程式碼分開存
from .database_async import get_db  # 假設你把上面程式碼分開存

# 設定參數
SECRET_KEY = "mykey"  # 實務上請使用環境變數
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
# 使用Passlib 函式庫, 建立一個加密Object(指定使用的Schema, 以及自動deprecated)
# 註冊後可使用pwd_context.hash("password123")：將明文密碼轉為雜湊字串, 再存入資料庫。
# 後續可使用pwd_context.verify("password123", hashed_password)：驗證使用者輸入的密碼是否正確。
# 將 argon2 放在第一順位
pwd_context = CryptContext(schemes=["argon2", "bcrypt"], deprecated="auto")
# pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# OAuth2PasswordBearer：這是一個「依賴項（Dependency）」，
# 它告訴 FastAPI：當前端發送請求時，去 HTTP Header 的 Authorization 欄位找 Bearer <Token>。
# tokenUrl="token"：這指定了取得 Token 的 API 路徑。
# 當你在 Swagger UI (/docs) 點擊右上角的 Authorize 按鈕時，
# 它會知道要往 your-api-url/token 發送帳號密碼來交換 Token。
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

app = FastAPI()

# 使用Dict來存放USER資料庫, user是testuser,
# 明文password是123456, 字典中放的Password則已加密, 此處用於Debug可取到Hashed Password的值, 才能到DB中建立 User資料
users_db = {
    "testuser": {"username": "testuser", "password": pwd_context.hash("123456")}
}


# 工具函數：產生 JWT, 供login成功後,Call它產生JWT token回傳
# 傳入JWT payload 中的字典資料
def create_access_token(data: dict):
    # Python字典copy method建立另一字典變數
    to_encode = data.copy()
    # 舊的寫法（不推薦）
    # expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    # 新的推薦寫法：使用 timezone-aware 物件
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    # python 字典update method, 更新特定Key的值,不存在則加入
    to_encode.update({"exp": expire})
    # 使用encode method將字典變數, 依SECRET KEY加密後回傳
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# 這是受保護路由的核心邏輯
# 使用二個Depends()依賴項: "驗證去取得傳入的JWTToken","取得連線的資料庫Session",傳入Function參數中
async def get_current_user(
    token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)
):
    credentials_exception = HTTPException(
        status_code=401,
        detail="無法驗證憑證",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # 解碼 JWT Token, 取得 payload 中的 username 資料, 如果沒有則丟出驗證失敗的例外
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # 非同步查詢資料庫：驗證從 JWT Token 取得的 username 是否存在於資料庫中, 如果不存在則丟出驗證失敗的例外
    result = await db.execute(
        select(models.User).filter(models.User.username == username)
    )
    user = result.scalars().first()

    if user is None:
        raise credentials_exception
    # 如果驗證成功，回傳 user 物件給呼叫它的路由函數使用
    return user


# 1. 登入路由：取得 Token
# Depends()中未傳入參數, 會自動取它前方參數的型別變數OAuth2PasswordRequestForm做為依賴項
# 它是類別Class, 具有__call__() method會自動去取出Request Body 中尋找
# Content-Type: application/x-www-form-urlencoded 的資料，
# 並強制要求前端必須傳送 username 和 password 欄位回傳字典型態資料值給form_data參數
@app.post("/token")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),  # 改用 AsyncSession 型別
):
    # 使用非同步查詢
    query = select(models.User).filter(models.User.username == form_data.username)
    result = await db.execute(query)
    user = result.scalars().first()

    if not user or not pwd_context.verify(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="帳號或密碼錯誤")

    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


# 採用JWT Token驗證的受保護路由：需要在HTTP Header中帶上有效的 Bearer <Token> 才能訪問
@app.get("/users/me")
async def read_users_me(current_user: models.User = Depends(get_current_user)):
    # 這裡的 current_user 已經是從資料庫查出來的 ORM 物件了
    return {
        "username": current_user.username,
        "id": current_user.id,
        "is_active": current_user.is_active,
        "msg": "這是一條來自 Supabase 的受保護資料",
    }
