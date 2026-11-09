from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.pool import NullPool

# 1. 改用 asyncpg 協定，Port 依然建議用 6543 (Pooler)
# SQLALCHEMY_DATABASE_URL = "postgresql+asyncpg://postgres.cvotzsjolzsvevoefucb:VW7spP9X7I3t4Vqp@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres?prepared_statement_cache_size=0&statement_cache_size=0"
SQLALCHEMY_DATABASE_URL = "postgresql+asyncpg://postgres.cvotzsjolzsvevoefucb:VW7spP9X7I3t4Vqp@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres"


# 2. 建立非同步引擎
engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    echo=True,
    # 第一部分：處理 asyncpg 與 PgBouncer 的相容性 (解決 PreparedStatement 錯誤)
    connect_args={"prepared_statement_cache_size": 0, "statement_cache_size": 0},
    # 第二部分：處理連線池的管理與穩定性 (防止連線死掉或爆量)
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

# 3. 建立非同步 Session 工廠
AsyncSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, autocommit=False, autoflush=False
)

# 3b. 排程專用的 Session 工廠（2026-11）：APScheduler 的 bridge 每次在別的執行緒開一個
# 新的 event loop，如果從上面的連線池拿到 uvicorn 主 loop 建立的連線，asyncpg 會報
# 「Future attached to a different loop」而失敗。這裡用 NullPool：不保留連線，每次都在
# 當下的 loop 開新連線、用完就關，跨 loop 也不會出錯。
scheduler_engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"prepared_statement_cache_size": 0, "statement_cache_size": 0},
    poolclass=NullPool,
)
SchedulerSessionLocal = async_sessionmaker(
    bind=scheduler_engine, class_=AsyncSession, autocommit=False, autoflush=False
)

Base = declarative_base()


# 4. 修改 Dependency 為非同步版本
async def get_db():
    async with AsyncSessionLocal() as db:
        try:
            yield db
            # 如果一切正常，FastAPI 結束後會回到這裡
            # 如果你有寫入操作，也可以在這裡統一 commit
        except Exception as e:
            # 關鍵點：發生任何錯誤，立刻回滾，清空該連線的 Transaction 狀態
            await db.rollback()
            raise e
        finally:
            # 確保連線一定會關閉並釋放回 Pool
            await db.close()
