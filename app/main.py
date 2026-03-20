# from fastapi import FastAPI

# app = FastAPI()


# @app.get("/")
# async def root():
#     return {"message": "Hello World"}

# @app.get("/users/20162280/ironman/6767")
# async def root():
#     return {"message": "Hi user~"}

# @app.get("/users/{user_id}")
# async def root(user_id):
#     return {"message": f"Hi user {user_id}"}

from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel
import os
import shutil
import uuid

#直接指定package.module, import它所建立的router物件
from app.api.v1.api import api_router

app = FastAPI(
    title="My Professional API",
    description="使用 v1 版本控制架構的 FastAPI 專案",
    version="1.0.0"
)

# 掛載總路由，通常會加上 /api/v1 前綴
app.include_router(api_router, prefix="/api/v1")

@app.get("/")
def root():
    return {"message": "Welcome to the API Service"}

class User(BaseModel):
    name: str
    age: int


@app.post("/user")
async def create_user(user: User) -> User:
    return user


@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    # 讀取檔案內容或儲存檔案
    return {
        "filename": file.filename,
        "content_type": file.content_type
    }

from fastapi import FastAPI, Form, File, UploadFile

@app.post("/profile")
async def update_profile(
    user_id: int = Form(...),
    bio: str = Form(None),
    avatar: UploadFile = File(...)
):
    return {
        "user_id": user_id,
        "bio": bio,
        "avatar_name": avatar.filename
    }

# 定義上傳檔案存放的目錄
UPLOAD_DIR = "uploads"

# 如果目錄不存在則建立它
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)

@app.post("/upload-to-disk")
async def save_file_to_disk(file: UploadFile = File(...)):
    # 1. 定義完整的檔案存放路徑 (資料夾 + 原始檔名)
    # 產生唯一檔名：uuid_原始檔名 
    unique_filename = f"{uuid.uuid4()}_{file.filename}" 
    file_path = os.path.join(UPLOAD_DIR, unique_filename)
    
    # 2. 開啟一個新檔案並將上傳的內容寫入
    with open(file_path, "wb") as buffer:
        # 使用 shutil.copyfileobj 自動處理串流寫入，效率最高
        shutil.copyfileobj(file.file, buffer)
    
    return {
        "message": "檔案上傳成功",
        "path": file_path,
        "filename": file.filename
    }
