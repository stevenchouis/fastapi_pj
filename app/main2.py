from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database_async import get_db

app = FastAPI(title="FastAPI Async Supabase Server")


@app.get("/db-check")
async def db_check(db: AsyncSession = Depends(get_db)):
    try:
        # 使用 await 執行非同步查詢
        result = await db.execute(text("SELECT now();"))
        time = result.scalar()
        return {"status": "connected (Async)", "server_time": time}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
