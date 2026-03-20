from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext

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
# 明文password是123456, 字典中放的Password則已加密
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
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # 去users_db 這字典變數中用username去取到它的值, 值也是個字典變數
    user = users_db.get(form_data.username)
    # user["password"將DB中己加密的password, 和傳入的form_data中的明文password
    # 傳給 pwd_context.verify(), 它會自動用當初加密方法解密比較, 若相同則符合
    print(f"DEBUG: 密碼長度為 {len(form_data.password)}")
    # 如果這裡印出來超過 72，就會觸發你看到的 ValueError
    if not user or not pwd_context.verify(form_data.password[:72], user["password"]):
        raise HTTPException(status_code=400, detail="帳號或密碼錯誤")
    # login成功, 需使用username放成key :”sub”的值, call create_access_token()
    # 組成加密後的JWT 三段TOKEN, 傳回前端,後續其它API請求取到它即可
    # 放在Header 的bearer中, 再CALL API
    access_token = create_access_token(data={"sub": user["username"]})
    return {"access_token": access_token, "token_type": "bearer"}


# 2. 受保護的路由：需要驗證 Token(此即為後續其它API請求)
@app.get("/users/me")
async def read_users_me(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="無效的 Token")
    except JWTError:
        raise HTTPException(status_code=401, detail="無法驗證憑證")

    return {"username": username, "msg": "這是受保護的資料"}
