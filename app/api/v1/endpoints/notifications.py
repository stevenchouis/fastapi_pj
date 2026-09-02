from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

# 修正重點：從 sqlalchemy 明確匯入 select
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.api.deps import get_current_user  # 確保引用路徑正確
from app.database_async import get_db

# 在 Python 中，每個 . 代表往上一層。
# 向上跳三層到 app/ 找 database_async.py
from ....database_async import AsyncSessionLocal

# 向上跳三層到 app/ 找 services/ 目錄
from ....services.push_service import send_user_push_notifications

router = APIRouter()


@router.post("/tasks/{task_type}/complete")
async def handle_task_completion(
    task_type: str, user_id: int, background_tasks: BackgroundTasks
):
    """
    範例情境：
    task_type="feeder" -> 阿肥吃飽了
    task_type="stock"  -> 觸發買入訊號
    """

    # 根據不同任務設定通知內容
    # 在 Python 中，只要用 小括號 () 包裹，且內部元素用逗號隔開，就會形成一個 tuple
    # print(type(content["feeder"]))  # 輸出: <class 'tuple'>
    # 使用Dictionary 的get(keyname,value) method:
    # keyname: 用於找該Dictionary的key,若找到則傳回該key的Value
    # value   : 若前述找不到, 則直接return 你放的這個value
    # 通知系統中，使用 tuple 是個很好的選擇，因為：
    # 不可變性 (Immutable)：通知的標題與內文範本定義好後就不會被變動。
    # 解構賦值 (Unpacking)：你可以很方便地把這兩個值取出來, 例如：title, body = content[task_type]

    content = {
        "feeder": ("餵食成功 🐾", "阿肥已經吃完飯囉！"),
        "stock": ("策略觸發 📈", "您的股票已達成設定條件。"),
    }.get(task_type, ("通知", "您的任務已完成"))

    title, body = content

    # 關鍵：將推播任務丟到背景，不占用 API 回傳時間
    # 參數說明:
    # 1. send_user_push_notifications: 這是我們定義的背景任務函數
    # 2. AsyncSessionLocal: 傳入Session,以便在背景開啟新的連線
    # 3. user_id, title, body: 這些是推播內容
    # 4. {"type": task_type, "screen": "NotificationInbox"}: 這是帶給 App 的跳轉參數，讓 App 知道點擊通知後要導向哪個畫面
    background_tasks.add_task(
        send_user_push_notifications,
        AsyncSessionLocal,  # 傳入工廠以便在背景開啟新的連線到DB
        user_id,
        title,
        body,
        {"type": task_type, "screen": "NotificationInbox"},  # 帶給 App 的跳轉參數
    )

    return {"status": "success", "message": "任務處理中，推播將於背景發送"}


# @router.get("/inbox", response_model=List[schemas.NotificationLog])
# async def get_user_notifications(
#     db: AsyncSession = Depends(get_db),
#     current_user: models.User = Depends(get_current_user),
# ):
#     result = await db.execute(
#         select(models.NotificationLog)
#         .where(models.NotificationLog.user_id == current_user.id)
#         .order_by(models.NotificationLog.created_at.desc())
#         .limit(50)
#     )
#     return result.scalars().all()
@router.get("/inbox")  # 確保路徑與前端 fetch 一致
async def get_user_notifications(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        print(f"DEBUG: 正在查詢 User ID: {current_user.id} 的通知")
        result = await db.execute(
            select(models.NotificationLog)
            .where(models.NotificationLog.user_id == current_user.id)
            .order_by(models.NotificationLog.created_at.desc())
        )
        notifications = result.scalars().all()
        print(f"DEBUG: 找到 {len(notifications)} 筆通知")
        return notifications
    except Exception as e:
        print(f"ERROR: {str(e)}")  # 這行會讓你在後端終端機看到真正的 500 原因
        raise HTTPException(status_code=500, detail="後端處理出錯")


@router.put("/{notification_id}/read", response_model=schemas.NotificationLog)
async def mark_notification_as_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # 1. 尋找屬於該使用者的特定通知
    result = await db.execute(
        select(models.NotificationLog)
        .where(models.NotificationLog.id == notification_id)
        .where(models.NotificationLog.user_id == current_user.id)
    )
    notification = result.scalars().first()

    if not notification:
        raise HTTPException(status_code=404, detail="找不到此通知或無權限存取")

    # 2. 標記為已讀
    notification.is_read = True

    # 3. 儲存變更
    await db.commit()
    await db.refresh(notification)

    return notification


@router.put("/read-all")
async def mark_all_notifications_as_read(
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # 修正 Ruff E712：使用 .is_(False) 或 == False 的 SQLAlchemy 寫法
    # 在 update 語法中，建議寫法如下：
    await db.execute(
        update(models.NotificationLog)
        .where(models.NotificationLog.user_id == current_user.id)
        .where(models.NotificationLog.is_read.is_(False))  # 修正 Ruff 報錯點
        .values(is_read=True)
    )
    await db.commit()

    return {"status": "success", "message": "所有通知已標記為已讀"}


@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # 執行刪除指令
    result = await db.execute(
        delete(models.NotificationLog)
        .where(models.NotificationLog.id == notification_id)
        .where(models.NotificationLog.user_id == current_user.id)
    )
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="找不到通知或無權限")

    return {"status": "success", "message": "通知已刪除"}
