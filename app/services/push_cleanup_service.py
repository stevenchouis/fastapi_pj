"""
2026-11 清除失效的推播 token（詳見 docs/push-token-cleanup.md）。

三條清除路徑：
1. Ticket 階段：發送當下 Expo 回 DeviceNotRegistered → push_service 直接刪（不在這支）
2. Receipt 階段：check_push_receipts_task，排程每 15 分鐘查一次已滿 15 分鐘的 ticket，
   receipt 是 DeviceNotRegistered 就刪 token；超過 24 小時還查不到的 ticket 直接丟掉
3. 90 天沒出現：delete_stale_push_tokens_task，每日排程刪除 last_seen_at 超過 90 天的 token

MessageTooBig、InvalidCredentials 等其他錯誤只記 log，不刪 token。
"""
import asyncio
import logging
from collections import namedtuple
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app import models
from app.core.monitoring import capture_exception, capture_message
from app.services.push_service import DEVICE_NOT_REGISTERED, push_client

logger = logging.getLogger(__name__)

RECEIPT_DELAY = timedelta(minutes=15)  # Expo 建議發送後至少 15 分鐘再查
RECEIPT_TTL = timedelta(hours=24)  # Expo 只保留 receipt 24 小時
RECEIPT_BATCH = 1000  # 每次最多查幾筆（Expo getReceipts 單次上限 1000）
STALE_AFTER = timedelta(days=90)

# exponent_server_sdk 的 check_receipts_multiple 只需要物件有 .id
_TicketRef = namedtuple("_TicketRef", ["id"])


async def check_push_receipts_task(db_factory, client=push_client) -> dict:
    """查 Expo receipt，回傳統計（給驗證與 log 用）。網路呼叫時不佔用 DB 連線。"""
    now = datetime.now(UTC)
    async with db_factory() as db:
        expired = await db.execute(
            delete(models.PushTicket).where(models.PushTicket.created_at <= now - RECEIPT_TTL)
        )
        rows = (
            await db.execute(
                select(models.PushTicket.ticket_id, models.PushTicket.token)
                .where(models.PushTicket.created_at <= now - RECEIPT_DELAY)
                .order_by(models.PushTicket.created_at)
                .limit(RECEIPT_BATCH)
            )
        ).all()
        await db.commit()
    stats = {"expired": expired.rowcount or 0, "checked": len(rows), "receipts": 0, "dead_tokens": 0}
    if not rows:
        return stats

    token_by_ticket = {r.ticket_id: r.token for r in rows}
    try:
        loop = asyncio.get_running_loop()
        receipts = await loop.run_in_executor(
            None, client.check_receipts_multiple, [_TicketRef(t) for t in token_by_ticket]
        )
    except Exception as e:
        # 整批查詢失敗（Expo 掛掉、網路問題）：ticket 留著下次再查，24 小時後自然丟掉
        logger.error(f"查詢 Expo push receipt 失敗: {e}")
        capture_exception(e, area="push_receipt")
        return stats

    done_ticket_ids, dead_tokens = [], set()
    for receipt in receipts:
        if receipt.id not in token_by_ticket:
            continue
        done_ticket_ids.append(receipt.id)
        if receipt.status != "ok":
            error = (receipt.details or {}).get("error")
            logger.warning(f"Expo receipt 錯誤 {error}: {receipt.message}")
            if error == DEVICE_NOT_REGISTERED:
                dead_tokens.add(token_by_ticket[receipt.id])  # 預期中的情況，不送 Sentry
            else:
                capture_message(f"Expo push receipt 錯誤: {error} {receipt.message}", area="push_receipt")
    # 沒出現在回應裡的 ticket 代表 receipt 還沒好，留著下次再查

    async with db_factory() as db:
        try:
            if dead_tokens:
                await db.execute(
                    delete(models.PushToken).where(models.PushToken.token.in_(dead_tokens))
                )
            if done_ticket_ids:
                await db.execute(
                    delete(models.PushTicket).where(models.PushTicket.ticket_id.in_(done_ticket_ids))
                )
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(f"套用 push receipt 結果失敗: {e}")
            capture_exception(e, area="push_receipt")
            return stats
    stats.update(receipts=len(done_ticket_ids), dead_tokens=len(dead_tokens))
    if dead_tokens:
        logger.info(f"依 receipt 刪除 {len(dead_tokens)} 個 DeviceNotRegistered 的推播 token")
    return stats


async def delete_stale_push_tokens_task(db_factory) -> int:
    """刪除超過 90 天沒有重新註冊（App 沒打開過）的 token，回傳刪除筆數。"""
    async with db_factory() as db:
        try:
            result = await db.execute(
                delete(models.PushToken).where(
                    models.PushToken.last_seen_at < datetime.now(UTC) - STALE_AFTER
                )
            )
            await db.commit()
        except Exception as e:
            await db.rollback()
            logger.error(f"刪除過期推播 token 失敗: {e}")
            capture_exception(e, area="push_cleanup")
            return 0
    count = result.rowcount or 0
    if count:
        logger.info(f"刪除 {count} 個超過 90 天沒出現的推播 token")
    return count
