from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# 絕對路徑導入，確保在 Render 或本地執行都不會出錯
from app import models, schemas

# 假設你已有取得當前使用者的 Dependency
from app.api.deps import get_current_user
from app.database_async import get_db

# 這裡不寫 prefix，因為會由上一層 api.py 統一分配
router = APIRouter()


@router.get("/")
async def get_users():
    return [{"id": 1, "username": "admin"}, {"id": 2, "username": "guest"}]


# 採用JWT Token驗證的受保護路由：需要在HTTP Header中帶上有效的 Bearer <Token> 才能訪問
@router.get("/me")
async def read_users_me(current_user: models.User = Depends(get_current_user)):
    # 這裡的 current_user 已經是從資料庫查出來的 ORM 物件了
    return {
        "username": current_user.email,
        "id": current_user.id,
        "is_active": current_user.is_active,
        "msg": "這是一條來自 Supabase 的受保護資料",
    }


@router.get("/{user_id}")
async def get_user_by_id(user_id: int):
    return {"id": user_id, "username": f"user_{user_id}"}


@router.post("/push-tokens", status_code=status.HTTP_204_NO_CONTENT)
async def update_user_push_token(
    payload: schemas.PushTokenCreate,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    更新或新增使用者的推播 Token (Upsert 邏輯)
    """
    # 1. 檢查該 Token 是否已存在
    query = select(models.PushToken).where(models.PushToken.token == payload.token)
    result = await db.execute(query)
    existing_record = result.scalars().first()

    if existing_record:
        # 2. 如果存在，更新關聯的使用者與裝置資訊
        # (預防同一台手機切換不同帳號登入的情境)
        existing_record.user_id = current_user.id
        existing_record.device_name = payload.device_name
    else:
        # 3. 如果是新 Token，建立新紀錄
        new_token = models.PushToken(
            token=payload.token,
            device_name=payload.device_name,
            user_id=current_user.id,
        )
        db.add(new_token)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="無法儲存推播 Token",
        )

    return  # 返回 204 No Content
