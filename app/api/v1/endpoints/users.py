import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# 絕對路徑導入，確保在 Render 或本地執行都不會出錯
from app import models, schemas
from app.api import deps

# 假設你已有取得當前使用者的 Dependency
from app.api.deps import get_current_user
from app.core.security import pwd_context  # 引用你現有的物件
from app.database_async import AsyncSessionLocal, get_db
from app.schemas.user import User, UserUpdate  # 確保匯入新 Schema
from app.services.coupon_service import build_coupon
from app.services.push_service import send_user_push_notifications

# 設定 logger 方便在終端機看到底發生什麼事
logger = logging.getLogger(__name__)

# 這裡不寫 prefix，因為會由上一層 api.py 統一分配
router = APIRouter()


@router.get("/")
async def get_users():
    return [{"id": 1, "username": "admin"}, {"id": 2, "username": "guest"}]


# 採用JWT Token驗證的受保護路由：需要在HTTP Header中帶上有效的 Bearer <Token> 才能訪問
@router.get("/me", response_model=User)  # 確保這裡指定了 response_model
async def read_users_me(current_user: models.User = Depends(get_current_user)):
    # 為了測試，我們先確認 ORM 物件裡是否有值
    print(f"DEBUG: 資料庫中的網址為: {current_user.avatar_url}")

    # 推薦做法：直接回傳 current_user，FastAPI 會根據 User Schema 自動轉換
    return current_user


@router.put("/me", response_model=User)
async def update_user_me(
    obj_in: UserUpdate,
    current_user: models.User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(deps.get_db),  # 確保這裡是 AsyncSession
):
    print(f"DEBUG: obj_in 資料內容 -> {obj_in.model_dump()}")

    if obj_in.avatar_url is not None:
        current_user.avatar_url = obj_in.avatar_url
        print(f"DEBUG: 已將 avatar_url 設為 -> {obj_in.avatar_url}")

    # 執行儲存 (必須加上 await)
    try:
        db.add(current_user)
        await db.commit()  # <--- 修正處：加上 await
        await db.refresh(current_user)  # <--- 修正處：加上 await
        print("DEBUG: 資料庫更新成功！")
        return current_user
    except Exception as e:
        await db.rollback()  # <--- 修正處：加上 await
        print(f"DEBUG: 更新失敗，原因: {e}")
        raise HTTPException(status_code=500, detail="Database Update Failed")


@router.get("/{user_id}")
async def get_user_by_id(user_id: int):
    return {"id": user_id, "username": f"user_{user_id}"}


@router.post("/register", response_model=schemas.User)
async def create_user(
    user_in: schemas.UserCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # 檢查 Email 是否已被使用
    query = select(models.User).where(models.User.email == user_in.email)
    result = await db.execute(query)
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="此 Email 已被註冊")

    # 建立 User 物件（不包含 username）
    db_user = models.User(
        email=user_in.email,
        hashed_password=pwd_context.hash(user_in.password),
        birthday=user_in.birthday,  # 新增生日欄位
        is_active=True,
    )
    db.add(db_user)
    await db.flush()  # 取得 user.id，同一交易內接著建立新會員歡迎禮券
    user_id = db_user.id

    welcome_coupon = build_coupon(
        user_id=user_id,
        title="新會員歡迎禮券",
        discount_amount=100.0,
        valid_days=30,
    )
    db.add(welcome_coupon)
    await db.flush()  # 取得 coupon.id
    coupon_id = welcome_coupon.id

    await db.commit()
    await db.refresh(db_user)

    # 註冊當下通常還沒有 Push Token（要等使用者登入後才會同步），
    # send_user_push_notifications 查無 token 會靜默跳過，只會留下 NotificationLog
    background_tasks.add_task(
        send_user_push_notifications,
        AsyncSessionLocal,
        user_id,
        "🎉 歡迎加入！",
        "新會員歡迎禮券已存入您的帳戶",
        {"coupon_id": coupon_id, "screen": "Coupons"},
    )

    return db_user


@router.post("/push-tokens", status_code=status.HTTP_204_NO_CONTENT)
async def update_user_push_token(
    payload: schemas.PushTokenCreate,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    更新或新增使用者的推播 Token (Upsert 邏輯)。
    當同一個裝置換人登入，或同一個人換裝置時，都能正確綁定。
    """
    try:
        # 1. 查詢資料庫是否已存在該 Token
        # 使用 select(models.PushToken) 必須確保上方有 from sqlalchemy import select
        query = select(models.PushToken).where(models.PushToken.token == payload.token)
        result = await db.execute(query)
        existing_record = result.scalars().first()

        if existing_record:
            # 2. 如果 Token 已存在，更新其關聯的使用者 ID 與裝置名稱
            # 這樣可以處理「同一台手機切換不同帳號登入」的情況
            existing_record.user_id = current_user.id
            existing_record.device_name = payload.device_name
            # 在某些 SQLAlchemy 配置中，修改屬性後建議執行 add 以確保追蹤
            db.add(existing_record)
            logger.info(
                f"更新現有 Token: {payload.token[:15]}... 對應使用者 ID: {current_user.id}"
            )
        else:
            # 3. 如果是全新 Token，建立新紀錄
            new_token = models.PushToken(
                token=payload.token,
                device_name=payload.device_name,
                user_id=current_user.id,
            )
            db.add(new_token)
            logger.info(
                f"新增全新 Token: {payload.token[:15]}... 對應使用者 ID: {current_user.id}"
            )

        # 4. 提交到資料庫
        await db.commit()
        return None  # HTTP 204 不回傳 Body

    except IntegrityError as ie:
        # 處理併發衝突（例如兩個請求同時寫入同一個新 Token）
        await db.rollback()
        logger.warning(f"Push Token 併發衝突: {str(ie)}")
        # 衝突通常代表資料已存在，對前端來說結果是一樣的，可以回傳成功或 204
        return None

    except Exception as e:
        # 捕捉其餘未知錯誤並記錄
        await db.rollback()
        logger.error(f"儲存 Push Token 時發生未知錯誤: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"伺服器內部錯誤: {str(e)}",
        )
