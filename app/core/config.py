from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 取得專案根目錄路徑 (假設 config.py 在 app/core/)
# .parent.parent.parent 會指到 FASTAPI_PJ 根目錄
base_dir = Path(__file__).resolve().parent.parent.parent
env_file_path = base_dir / ".env"


class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    # 改為可選或提供預設值，避免啟動直接崩潰，或者保持原樣強制要求
    DATABASE_URL: str

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
