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

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.declarative import declarative_base

# 1. 改用 asyncpg 協定，Port 依然建議用 6543 (Pooler)
SQLALCHEMY_DATABASE_URL = "postgresql+asyncpg://postgres.cvotzsjolzsvevoefucb:VW7spP9X7I3t4Vqp@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres"


# 2. 建立非同步引擎
engine = create_async_engine(SQLALCHEMY_DATABASE_URL, echo=True)

# 3. 建立非同步 Session 工廠
AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, autocommit=False, autoflush=False
)

Base = declarative_base()


# 4. 修改 Dependency 為非同步版本
async def get_db():
    async with AsyncSessionLocal() as db:
        yield db
