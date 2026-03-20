import logging
from typing import List, Optional

import httpx
from exponent_server_sdk import (
    DeviceNotRegisteredError,
    PushClient,
    PushMessage,
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models

logger = logging.getLogger(__name__)


async def cleanup_invalid_tokens(db: AsyncSession, tokens: List[str]):
    """當 Expo 回報 Token 失效時，從資料庫徹底移除"""
    if tokens:
        await db.execute(
            delete(models.PushToken).where(models.PushToken.token.in_(tokens))
        )
        await db.commit()
        logger.info(f"已清理無效 Tokens: {len(tokens)} 筆")


async def send_user_push_notifications(
    db_factory,  # 傳入 sessionmaker 或是 session
    user_id: int,
    title: str,
    body: str,
    data: Optional[dict] = None,
):
    """主邏輯：撈取使用者所有裝置並發送"""
    # 建立新的 Session (因為是在 Background Task 執行)
    async with db_factory() as db:
        # 1. 撈取該使用者的所有 Token
        from sqlalchemy import select

        result = await db.execute(
            select(models.PushToken).where(models.PushToken.user_id == user_id)
        )
        db_tokens = result.scalars().all()

        if not db_tokens:
            return

        token_list = [t.token for t in db_tokens]
        invalid_tokens = []

        # 2. 使用 httpx 非同步發送
        async with httpx.AsyncClient() as client:
            push_client = PushClient(session=client)

            for token in token_list:
                try:
                    # 封裝訊息
                    msg = PushMessage(
                        to=token,
                        title=title,
                        body=body,
                        data=data or {},
                        sound="default",
                    )
                    # 發送
                    response = await push_client.publish_async(msg)
                    # 檢查回應內容 (Expo 有時會在此處回報失效)
                    response.validate_response()

                except DeviceNotRegisteredError:
                    # 發現無效 Token，加入待清理清單
                    invalid_tokens.append(token)
                except Exception as e:
                    logger.error(f"發送推播至 {token} 失敗: {e}")

        # 3. 清理無效 Token
        if invalid_tokens:
            await cleanup_invalid_tokens(db, invalid_tokens)
