from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select  # 確保在檔案頂部匯入
from sqlalchemy.orm import Session

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


# 1. 登入路由：取得 Token
# Depends()中未傳入參數, 會自動取它前方參數的型別變數OAuth2PasswordRequestForm做為依賴項
# 它是類別Class, 具有__call__() method會自動去取出Request Body 中尋找
# Content-Type: application/x-www-form-urlencoded 的資料，
# 並強制要求前端必須傳送 username 和 password 欄位回傳字典型態資料值給form_data參數
@app.post("/token")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    # 1. 使用 select 建立查詢
    statement = select(models.User).filter(models.User.email == form_data.username)

    # 2. 使用 await 執行，並取得第一個結果
    result = await db.execute(statement)
    user = result.scalars().first()

    # 驗證邏輯不變，只是 user 現在是個物件而非字典
    if not user or not pwd_context.verify(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="帳號或密碼錯誤")

    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/users/me")
async def read_users_me(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="無效的 Token")
    except JWTError:
        raise HTTPException(status_code=401, detail="無法驗證憑證")

    # 從資料庫確認使用者依然存在
    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        raise HTTPException(status_code=401, detail="使用者不存在")

    return {"username": user.username, "id": user.id}
