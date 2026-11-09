import logging
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import delete, or_, select, update
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
from app.services import einvoice_service, referral_service
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

    # 空字串（trim 後）代表清除暱稱；None 代表這次沒帶，不動
    if obj_in.nickname is not None:
        current_user.nickname = obj_in.nickname or None

    # 2026-11 電子發票載具：沒帶這個欄位不動；null／空字串（去空白後）清除；其他要符合手機條碼格式
    if "einvoice_carrier" in obj_in.model_fields_set:
        carrier = einvoice_service.normalize_carrier(obj_in.einvoice_carrier or "")
        if carrier and not einvoice_service.is_valid_carrier(carrier):
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "invalid_einvoice_carrier",
                    "message": "手機條碼格式錯誤，應為「/」加 7 碼英數字或 .+-",
                },
            )
        current_user.einvoice_carrier = carrier or None

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


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_me(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    刪除帳號（Google Play／App Store 上架要求的「App 內刪除帳號」）。立即生效，
    沒有冷靜期。role="staff" 的帳號禁止走這支（店員帳號要由管理者直接去 DB 處理，
    跟 role 本身沒有自助升級端點的慣例一致），回 403。

    「你決定」的三個設計點：
    1. 刪除標記沿用既有的 is_active（不新增 deleted_at）——login.py 三種登入
       方式已經在檢查這個欄位，這次只是在 deps.get_current_user 補上同一個
       檢查，讓刪除後的舊 JWT 也會被擋（401），不需要兩個欄位表達同一件事。
    2. Order／DineInOrder／StoreCheckout 的 user_id 維持指向這筆已匿名化的
       User，不改成 null——這三個欄位本來就是 nullable=False，改 null 需要
       額外 migration；而且 User 這邊已經清空所有識別資訊，保留「哪個帳號
       買的」這個事實本身不構成個資洩漏，跟一般電商「刪帳號不影響已完成
       訂單」的常見作法一致。
    3. 紅利點數：LoyaltyTransaction 明細、DailyCheckIn 簽到紀錄整批刪除、User.loyalty_balance 歸零
       ——跟 Order 等「交易紀錄」不同，點數明細純粹是會員制度內部帳本，沒有
       保留義務，整批清掉比只歸零餘額更乾淨。

    email 改成 `deleted-{id}@deleted.invalid` 這種不可能登入的佔位值，原 email
    因此可以重新註冊新帳號（email 欄位本身是 unique，佔位值用 id 保證不會跟
    其他已刪除帳號衝突）。MagicLinkToken 沒有 user_id 外鍵（只存 email 字串），
    所以要在改掉 email 前先記下舊值才能比對刪除。
    """
    if current_user.role == "staff":
        raise HTTPException(status_code=403, detail="店員帳號請由管理者處理")

    user_id = current_user.id
    old_email = current_user.email

    try:
        await db.execute(delete(models.PushToken).where(models.PushToken.user_id == user_id))
        # 2026-11 登入自動續期：續期證全部刪除，刪帳號後不能再換證
        await db.execute(delete(models.RefreshToken).where(models.RefreshToken.user_id == user_id))
        await db.execute(delete(models.NotificationLog).where(models.NotificationLog.user_id == user_id))
        await db.execute(delete(models.Favorite).where(models.Favorite.user_id == user_id))
        await db.execute(
            delete(models.Friendship).where(
                or_(models.Friendship.user_a_id == user_id, models.Friendship.user_b_id == user_id)
            )
        )
        # 只刪未使用的優惠券/核銷碼；已核銷的維持不動（跟 Order 一樣算交易紀錄）
        await db.execute(
            delete(models.Coupon).where(
                models.Coupon.user_id == user_id, models.Coupon.is_used.is_(False)
            )
        )
        await db.execute(
            delete(models.LoyaltyTransaction).where(models.LoyaltyTransaction.user_id == user_id)
        )
        # 2026-11 每日簽到紀錄：純會員活動紀錄，跟點數明細一樣不需保留
        await db.execute(delete(models.DailyCheckIn).where(models.DailyCheckIn.user_id == user_id))
        # 2026-11 推薦好友：被刪那方（推薦人或被推薦人）的推薦紀錄一併刪除；推薦碼在下面清空
        await db.execute(
            delete(models.Referral).where(
                or_(models.Referral.referrer_id == user_id, models.Referral.referee_id == user_id)
            )
        )
        if old_email:
            await db.execute(delete(models.MagicLinkToken).where(models.MagicLinkToken.email == old_email))
        # 2026-11 電子發票載具：訂單本身保留，但訂單上的載具快照清空——手機條碼在財政部
        # 對應到真實手機號碼，留著等於能把已匿名化的帳號連回本人；而且這期還沒真正
        # 開發票，快照沒有用途。之後串接開發票時，已開立發票的載具由發票紀錄保存，要重新評估
        for order_model in (models.Order, models.DineInOrder, models.StoreCheckout):
            await db.execute(
                update(order_model)
                .where(order_model.user_id == user_id, order_model.einvoice_carrier.isnot(None))
                .values(einvoice_carrier=None)
            )

        current_user.email = f"deleted-{user_id}@deleted.invalid"
        current_user.hashed_password = pwd_context.hash(secrets.token_urlsafe(32))
        current_user.nickname = None
        current_user.referral_code = None
        current_user.einvoice_carrier = None
        current_user.birthday = None
        current_user.avatar_url = None
        current_user.google_id = None
        current_user.line_id = None
        current_user.member_code_hash = None
        current_user.member_code_expires_at = None
        current_user.loyalty_balance = 0
        current_user.is_active = False

        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 刪除帳號失敗: {e}")
        raise HTTPException(status_code=500, detail="刪除帳號失敗")


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

    # 2026-11 推薦好友：有帶推薦碼就在同一個 transaction 記錄推薦關係＋發 $50 券；
    # 碼無效整個註冊 rollback（plan 規定），空字串視同沒帶
    if user_in.referral_code and user_in.referral_code.strip():
        outcome, _, _ = await referral_service.apply_referral(
            db, db_user, user_in.referral_code, check_window=False
        )
        if outcome != "ok":
            await db.rollback()
            raise HTTPException(
                status_code=400,
                detail={"error_code": "invalid_referral_code", "message": "推薦碼不存在"},
            )

    await db.commit()
    await db.refresh(db_user)

    # 註冊當下通常還沒有 Push Token（要等使用者登入後才會同步），
    # send_user_push_notifications 查無 token 會靜默跳過，只會留下 NotificationLog
    background_tasks.add_task(
        send_user_push_notifications,
        AsyncSessionLocal,
        user_id,
        "mynotification",
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
            existing_record.app_id = payload.app_id
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
                app_id=payload.app_id,
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
