from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

# python .（點）代表層級：在 Python 匯入系統中，
# 一個點 . 就已經完整代表了「當前路徑下的套件（Current Package）」
# 下例表示由models.py目前目錄下的database.py模組import Base Class
from .database_async import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)

    # 建立與 PushToken 的關聯
    push_tokens = relationship(
        "PushToken", back_populates="user", cascade="all, delete-orphan"
    )


class PushToken(Base):
    __tablename__ = "push_tokens"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    token = Column(
        String, unique=True, index=True, nullable=False
    )  # 儲存 ExpoPushToken[xxx...]
    device_name = Column(String, nullable=True)  # 可選：辨識裝置類型 (如 "iPhone 15")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="push_tokens")
