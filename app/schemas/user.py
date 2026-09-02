from datetime import date  # 匯入 date 類型以處理生日欄位

from pydantic import BaseModel, EmailStr


# 基礎模型：共用的欄位
class UserBase(BaseModel):
    email: EmailStr


# 建立使用者時需要傳入的資料（包含密碼）
class UserCreate(UserBase):
    birthday: date | None = None  # 新增生日欄位，允許為空
    password: str


# 新增：更新使用者時需要的資料（使用 | None 取代 Optional）
class UserUpdate(BaseModel):
    username: str | None = None
    avatar_url: str | None = None  # 接收來自 Supabase 的圖片網址
    birthday: date | None = None  # 新增生日欄位


# 回傳給前端的資料（排除密碼，增加 ID）
class User(UserBase):
    id: int
    # 必須新增這一行，GET /me 才會回傳這個欄位
    avatar_url: str | None = None
    birthday: date | None = None  # 新增生日欄位

    class Config:
        from_attributes = True  # 允許 Pydantic 讀取 SQLAlchemy 模型

    # 這裡的 from_attributes 是 Pydantic 的一個設定，允許它從 SQLAlchemy 模型的屬性讀取資料，
    # 這樣我們就可以直接將 SQLAlchemy 模型實例傳給 UserOut，而不需要手動轉換成 dict。
    # 最新的 Pydantic V2（FastAPI 目前的主流），官方更建議使用 model_config 變數來設定：
    # model_config = = ConfigDict(from_attributes=True)
