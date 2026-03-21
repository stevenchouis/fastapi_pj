# from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
# from sqlalchemy.orm import declarative_base
# from app.core.config import settings

# # 建立非同步引擎
# engine = create_async_engine(settings.async_database_url, echo=True)

# # 建立非同步 Session 工廠
# AsyncSessionLocal = async_sessionmaker(
#     bind=engine,
#     class_=AsyncSession,
#     expire_on_commit=False
# )

# Base = declarative_base()

# # Dependency: 提供非同步 DB Session
# async def get_db():
#     async with AsyncSessionLocal() as session:
#         try:
#             yield session
#             await session.commit()
#         except Exception:
#             await session.rollback()
#             raise
#         finally:
#             await session.close()

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 格式: postgresql://[user]:[password]@[host]:[port]/[db_name]
# SQLALCHEMY_DATABASE_URL = "postgresql://postgres:[你的密碼]@db.[你的專案ID].supabase.co:5432/postgres"
SQLALCHEMY_DATABASE_URL = "postgresql://postgres.cvotzsjolzsvevoefucb:VW7spP9X7I3t4Vqp@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres"
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# 取得資料庫連線的依賴項
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
