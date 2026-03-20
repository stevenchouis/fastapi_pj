from typing import Annotated
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import Self

app = FastAPI(title="Pydantic Validator Demo")

# --- 定義 Pydantic 模型 ---

class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, description="使用者名稱")
    password: str = Field(..., min_length=8, description="密碼")
    confirm_password: str = Field(..., min_length=8, description="確認密碼")
    age: int = Field(..., description="年齡")

    # 1. 單一欄位驗證：檢查名稱是否包含非法字元
    @field_validator('username')
    @classmethod
    def username_alphanumeric(cls, v: str) -> str:
        if not v.isalnum():
            raise ValueError('使用者名稱只能包含字母與數字')
        return v

    # 2. 單一欄位驗證：檢查年齡範圍
    @field_validator('age')
    @classmethod
    def check_age(cls, v: int) -> int:
        if v < 18:
            raise ValueError('註冊失敗：必須年滿 18 歲')
        return v

    # 3. 多欄位交互驗證：檢查兩次密碼是否一致
    # mode='after' 表示在基礎驗證（如長度檢查）完成後才執行此函數
    @model_validator(mode='after')
    def check_passwords_match(self) -> Self:
        if self.password != self.confirm_password:
            raise ValueError('兩次輸入的密碼不一致')
        return self

# --- 定義 API 路由 ---

@app.post("/register", status_code=status.HTTP_201_CREATED)
async def register(user: UserRegister):
    # 如果執行到這裡，代表所有驗證都已通過
    return {
        "message": "註冊成功！",
        "user_data": {
            "username": user.username,
            "age": user.age
        }
    }

