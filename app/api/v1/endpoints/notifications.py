from fastapi import APIRouter, BackgroundTasks

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
    content = {
        "feeder": ("餵食成功 🐾", "阿肥已經吃完飯囉！"),
        "stock": ("策略觸發 📈", "您的股票已達成設定條件。"),
    }.get(task_type, ("通知", "您的任務已完成"))

    title, body = content

    # 關鍵：將推播任務丟到背景，不占用 API 回傳時間
    background_tasks.add_task(
        send_user_push_notifications,
        AsyncSessionLocal,  # 傳入工廠以便在背景開啟新的連線
        user_id,
        title,
        body,
        {"type": task_type, "screen": "NotificationInbox"},  # 帶給 App 的跳轉參數
    )

    return {"status": "success", "message": "任務處理中，推播將於背景發送"}
