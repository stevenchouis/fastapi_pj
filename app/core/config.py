from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 取得專案根目錄路徑 (假設 config.py 在 app/core/)
# .parent.parent.parent 會指到 FASTAPI_PJ 根目錄
base_dir = Path(__file__).resolve().parent.parent.parent
env_file_path = base_dir / ".env"


class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str  # 不給預設值，強迫從 .env 讀取
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    # 改為可選或提供預設值，避免啟動直接崩潰，或者保持原樣強制要求
    DATABASE_URL: str
    # Google 登入用：驗證 id_token 的 audience 要用這組 Web Client ID
    GOOGLE_CLIENT_ID: str
    # Magic Link 登入用：Resend 的 API Key（尚未取得前先允許空字串，寄信時才報錯）
    RESEND_API_KEY: str = ""
    # Magic Link 落地頁的完整對外網址（開發階段用區網 IP，跟 App 的 EXPO_PUBLIC_API_URL 同一台主機）
    BASE_URL: str = "http://192.168.68.56:8000"
    # 管理者手動發券 API 用的固定密鑰（X-Admin-Key header 比對），不給預設值強迫從 .env 讀取
    ADMIN_API_KEY: str

    # 明確指定 env_file 的絕對路徑
    model_config = SettingsConfigDict(
        env_file=env_file_path,
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略多餘的環境變數
    )

    @property
    def async_database_url(self) -> str:
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url


settings = Settings()
