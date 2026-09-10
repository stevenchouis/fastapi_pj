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
            to=token,
            title=title,
            body=body,
            data=data or {},
            sound="default",
            # 沒有帶這個的話 Android 會把通知歸進系統預設的「Miscellaneous」分類，
            # 那個分類不支援橫幅（heads-up）顯示；前端已建立一個叫 "default" 的
            # channel（見 app/_layout.tsx 的 setNotificationChannelAsync），這裡
            # 要對應同一個名稱，Expo 才會把 channelId 一起送給 FCM
            channel_id="default",
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
    app_id: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
):
    """
    app_id 是必填的目標 App（"mynotification" / "staff-scanner"）——2026-09 起
    PushToken 開始記錄每個 token 屬於哪個 App（見 CLAUDE.md 推播通知一節的
    Phase 1/2/3 rollout），這裡只送給該使用者名下屬於這個 App 的 token，
    避免像過去那樣一個帳號同時裝兩個 App 時互相收到對方的推播。
    `app_id IS NULL` 的舊 token（還沒重新登入過、還沒被新版 App 覆蓋）不會收到，
    等使用者下次登入該 App、token 重新註冊後才會恢復。
    """
    token_list = []

    # 第一步：只負責撈資料，撈完立刻關閉連線釋放資源，避免 ROLLBACK
    async with db_factory() as db:
        # 1. 儲存到歷史紀錄表
        new_log = models.NotificationLog(
            user_id=user_id, title=title, body=body, data=data
        )
        db.add(new_log)
        result = await db.execute(
            select(models.PushToken).where(
                models.PushToken.user_id == user_id,
                models.PushToken.app_id == app_id,
            )
        )
        db_tokens = result.scalars().all()
        token_list = [t.token for t in db_tokens]
        # 即使只是查詢，也顯式提交一次來結束 Transaction
        await db.commit()
    if not token_list:
        logger.info(f"使用者 {user_id}（app_id={app_id}）無可用 Token")
        return

    # 第二步：在資料庫連線關閉後，才執行耗時的網路推播
    await _publish_to_tokens(token_list, title, body, data)


async def send_favorite_users_notifications(
    db_factory,
    product_id: int,
    app_id: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
):
    """
    推播給收藏了某商品、且該 token 屬於 app_id 這個 App 的所有使用者
    （用於商品到貨/降價通知，目前只會傳 "mynotification"）。
    每個收藏者各留一筆 NotificationLog，讓他們自己 App 內的通知歷史看得到
    （這筆紀錄不分 App，收藏者無論用哪個 App 開通知列表都看得到，只有「推播」
    這一步才依 app_id 篩選）。
    """
    async with db_factory() as db:
        result = await db.execute(
            select(models.Favorite.user_id).where(
                models.Favorite.product_id == product_id
            )
        )
        user_ids = [row[0] for row in result.all()]
        if not user_ids:
            await db.commit()
            logger.info(f"product_id={product_id} 沒有收藏者，略過推播")
            return

        for user_id in user_ids:
            db.add(
                models.NotificationLog(
                    user_id=user_id, title=title, body=body, data=data
                )
            )
        token_result = await db.execute(
            select(models.PushToken.token).where(
                models.PushToken.user_id.in_(user_ids),
                models.PushToken.app_id == app_id,
            )
        )
        token_list = [row[0] for row in token_result.all()]
        await db.commit()

    if not token_list:
        logger.info(f"product_id={product_id} 的收藏者在 app_id={app_id} 都沒有可用 Token")
        return

    await _publish_to_tokens(token_list, title, body, data)


async def send_role_push_notifications(
    db_factory,
    role: str,
    app_id: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
):
    """
    推播給某個角色、且該 token 屬於 app_id 這個 App 的所有使用者（目前用於堂食
    新訂單通知所有 role="staff" 的店員帳號，app_id="staff-scanner"）——避免店員
    帳號如果同時也裝了 mynotification（例如自己也是顧客），個人購物用的 App
    收到「有新訂單要備餐」這種跟它無關的推播。
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
                models.PushToken.user_id.in_(user_ids),
                models.PushToken.app_id == app_id,
            )
        )
        token_list = [row[0] for row in token_result.all()]
        await db.commit()

    if not token_list:
        logger.info(f"role={role} 的使用者在 app_id={app_id} 都沒有可用 Token")
        return

    await _publish_to_tokens(token_list, title, body, data)
