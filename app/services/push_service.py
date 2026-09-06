import asyncio
import logging
from typing import Optional

from exponent_server_sdk import PushClient, PushMessage
from sqlalchemy import select

from .. import models

logger = logging.getLogger(__name__)
push_client = PushClient()


async def _publish_to_tokens(
    token_list: list[str], title: str, body: str, data: Optional[dict] = None
):
    if not token_list:
        return

    loop = asyncio.get_event_loop()
    for token in token_list:
        msg = PushMessage(
            to=token, title=title, body=body, data=data or {}, sound="default"
        )
        try:
            # 使用 executor 執行同步發送
            response = await loop.run_in_executor(None, push_client.publish, msg)
            if response.status == "ok":
                logger.info("✅ 推播發送成功")
            else:
                logger.error(f"❌ Expo 錯誤: {response.message}")
        except Exception as e:
            logger.error(f"🔥 發送異常: {e}")


async def send_user_push_notifications(
    db_factory,
    user_id: int,
    title: str,
    body: str,
    data: Optional[dict] = None,
):
    token_list = []

    # 第一步：只負責撈資料，撈完立刻關閉連線釋放資源，避免 ROLLBACK
    async with db_factory() as db:
        # 1. 儲存到歷史紀錄表
        new_log = models.NotificationLog(
            user_id=user_id, title=title, body=body, data=data
        )
        db.add(new_log)
        result = await db.execute(
            select(models.PushToken).where(models.PushToken.user_id == user_id)
        )
        db_tokens = result.scalars().all()
        token_list = [t.token for t in db_tokens]
        # 即使只是查詢，也顯式提交一次來結束 Transaction
        await db.commit()
    if not token_list:
        logger.info(f"使用者 {user_id} 無可用 Token")
        return

    # 第二步：在資料庫連線關閉後，才執行耗時的網路推播
    await _publish_to_tokens(token_list, title, body, data)


async def send_role_push_notifications(
    db_factory,
    role: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
):
    """
    推播給某個角色的所有使用者（目前用於堂食新訂單通知所有 role="staff" 的店員帳號）。
    每個符合角色的使用者各留一筆 NotificationLog，讓他們自己 App 內的通知歷史看得到。
    """
    async with db_factory() as db:
        result = await db.execute(select(models.User.id).where(models.User.role == role))
        user_ids = [row[0] for row in result.all()]
        if not user_ids:
            await db.commit()
            logger.info(f"role={role} 無使用者，略過推播")
            return

        for user_id in user_ids:
            db.add(
                models.NotificationLog(
                    user_id=user_id, title=title, body=body, data=data
                )
            )
        token_result = await db.execute(
            select(models.PushToken.token).where(
                models.PushToken.user_id.in_(user_ids)
            )
        )
        token_list = [row[0] for row in token_result.all()]
        await db.commit()

    if not token_list:
        logger.info(f"role={role} 的使用者都沒有可用 Token")
        return

    await _publish_to_tokens(token_list, title, body, data)
