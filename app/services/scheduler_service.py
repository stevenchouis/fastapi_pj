import asyncio
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import extract, func, select, update

from app.core.config import settings
from app.database_async import AsyncSessionLocal  #
from app.models import Coupon, LoyaltyTransaction, User  #
from app.services.push_service import send_user_push_notifications

# 2026-10：先前沒有明確指定 timezone，實際觸發時間取決於執行機器的系統時區
# （本機開發機是 Asia/Taipei，但 Render 容器的實際時區未經證實，兩者可能不一致，
# 本機測試出來的觸發時機不能代表正式環境）。明確指定後，不管部署在哪個平台、
# 本機測試還是正式環境，所有 cron 排程的 hour/day 都是固定對應台灣當地時間，
# 不用再心算時區差。這個改動會讓既有兩支排程（生日禮券、點數到期）的「實際」
# 觸發時間變成確定值，若 Render 先前實際是 UTC，等於把執行時間往後移 8 小時——
# 跟使用者確認後才套用。
SCHEDULER_TIMEZONE = ZoneInfo("Asia/Taipei")


async def send_birthday_coupons_async_task():
    """執行非同步發券與通知任務"""
    # 記錄本次實際發券成功的對象，等資料庫交易 commit 完才觸發推播
    # （撈資料/寫DB → 關閉交易 → 再發推播，避免推播卡住交易）
    issued: list[tuple[int, int]] = []  # (user_id, coupon_id)

    async with AsyncSessionLocal() as db:
        try:
            today = date.today()
            # 計算下個月月份與年份
            next_month = (today.month % 12) + 1
            current_year = today.year if today.month < 12 else today.year + 1

            # 1. 篩選下月壽星 (Async 語法)
            query = select(User).where(extract("month", User.birthday) == next_month)
            result = await db.execute(query)
            birthday_users = result.scalars().all()

            # 計算下個月月底作為過期日
            if next_month == 12:
                last_day = date(current_year, 12, 31)
            else:
                last_day = date(current_year, next_month + 1, 1) - timedelta(days=1)
            expired_dt = datetime.combine(last_day, datetime.max.time())

            coupon_title = f"{next_month}月壽星專屬禮券"
            issued_count = 0

            for user in birthday_users:
                # 防呆：避免 job 被重複觸發（同一 process 內重疊執行、或未來多 process
                # 各自觸發）時，對同一位使用者、同一個月份重複發券。用 user_id +
                # title + expired_at（本次批次專屬的到期日）判斷是否已發過，
                # 不會擋到明年同月份的合法新券。
                dup_query = select(Coupon.id).where(
                    Coupon.user_id == user.id,
                    Coupon.title == coupon_title,
                    Coupon.expired_at == expired_dt,
                )
                dup_result = await db.execute(dup_query)
                if dup_result.first():
                    continue

                # 2. 建立新優惠券
                new_coupon = Coupon(
                    user_id=user.id,
                    title=coupon_title,
                    discount_amount=100.0,
                    expired_at=expired_dt,
                    is_used=False,
                )
                db.add(new_coupon)
                await db.flush()  # 取得 coupon_id 以便放入通知數據
                issued_count += 1
                issued.append((user.id, new_coupon.id))

            await db.commit()  #
            skipped_count = len(birthday_users) - issued_count
            print(
                f"[{datetime.now()}] 成功為 {issued_count} 位壽星發放優惠券"
                f"（跳過 {skipped_count} 位已發過本月禮券的重複觸發）"
            )
        except Exception as e:
            await db.rollback()  #
            print(f"排程任務執行失敗: {e}")
            return

    # 優惠券交易已 commit 完成後才發推播；send_user_push_notifications 會自己
    # 建立 NotificationLog 並觸發真實 Expo 推播，這裡不用再手動寫通知紀錄
    for user_id, coupon_id in issued:
        try:
            await send_user_push_notifications(
                AsyncSessionLocal,
                user_id,
                "mynotification",
                "🎂 生日禮物已送達！",
                "下個月就是您的生日，100元優惠券已存入您的帳戶。",
                {"coupon_id": coupon_id, "screen": "Coupons"},
            )
        except Exception as e:
            print(f"生日禮券推播失敗 user_id={user_id} coupon_id={coupon_id}: {e}")


def run_scheduler_bridge():
    """橋接 BackgroundScheduler (同步) 與 Async 任務"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(send_birthday_coupons_async_task())
    loop.close()


async def self_ping_task():
    """
    保活任務：定時打自己的 /health，避免 Render 免費方案閒置 15 分鐘後進入休眠。
    間隔（見 start_scheduler）刻意設在略短於 15 分鐘，讓服務維持醒著；本機開發
    環境 BASE_URL 通常是區網 IP，打失敗也只是印一行 log，不影響其他排程任務。
    """
    url = f"{settings.BASE_URL}/health"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
        print(f"[{datetime.now()}] 保活 ping {url} -> {response.status_code}")
    except Exception as e:
        print(f"[{datetime.now()}] 保活 ping 失敗 ({url}): {e}")


async def expire_loyalty_points_task():
    """
    點數到期排程：掃出所有已過期、還沒被折抵消耗完的 earn 紀錄（remaining_amount > 0
    且 expires_at 已過），把剩餘未消耗的部分收回（扣減 User.loyalty_balance、該筆
    remaining_amount 歸零），並各補一筆 type="expire" 的紀錄方便使用者對帳。
    """
    now = datetime.now(UTC)
    expired_count = 0

    async with AsyncSessionLocal() as db:
        try:
            query = select(LoyaltyTransaction).where(
                LoyaltyTransaction.type == "earn",
                LoyaltyTransaction.remaining_amount > 0,
                LoyaltyTransaction.expires_at <= now,
            )
            result = await db.execute(query)
            expired_txns = result.scalars().all()

            for txn in expired_txns:
                amount = txn.remaining_amount
                txn.remaining_amount = 0
                await db.execute(
                    update(User)
                    .where(User.id == txn.user_id)
                    .values(
                        loyalty_balance=func.greatest(User.loyalty_balance - amount, 0)
                    )
                )
                db.add(
                    LoyaltyTransaction(
                        user_id=txn.user_id,
                        type="expire",
                        amount=amount,
                        reason=f"點數過期（原賺點紀錄 #{txn.id}）",
                    )
                )
                expired_count += 1

            await db.commit()
            print(f"[{datetime.now()}] 點數到期排程完成，共收回 {expired_count} 筆賺點紀錄")
        except Exception as e:
            await db.rollback()
            print(f"點數到期排程執行失敗: {e}")


def run_loyalty_expiry_bridge():
    """橋接 BackgroundScheduler (同步) 與 expire_loyalty_points_task"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(expire_loyalty_points_task())
    loop.close()


POINTS_EXPIRY_REMINDER_WINDOW_DAYS = 7


async def send_points_expiry_reminder_task():
    """
    點數即將到期提醒排程：掃出 type="earn"、remaining_amount>0、expires_at 落在
    「今天～今天+7天」區間、且還沒提醒過（reminded_at IS NULL）的紀錄，同一使用者
    當天合併成一則推播（文案帶總到期點數）。用區間而不是「正好差 7 天」比對，
    是為了避免排程萬一哪天沒準時跑就漏掉使用者。reminded_at 是否標記跟推播有沒有
    真的送達無關——DB 這批紀錄一旦標記過，就不會再進到下一次排程的篩選範圍，
    跟 _publish_to_tokens 本身失敗只記 log、不回滾的既有慣例一致（best-effort 推播）。
    """
    now = datetime.now(UTC)
    window_end = now + timedelta(days=POINTS_EXPIRY_REMINDER_WINDOW_DAYS)
    reminded_count = 0
    notified_users = 0

    async with AsyncSessionLocal() as db:
        try:
            query = select(LoyaltyTransaction).where(
                LoyaltyTransaction.type == "earn",
                LoyaltyTransaction.remaining_amount > 0,
                LoyaltyTransaction.expires_at > now,
                LoyaltyTransaction.expires_at <= window_end,
                LoyaltyTransaction.reminded_at.is_(None),
            )
            result = await db.execute(query)
            due_txns = result.scalars().all()

            by_user: dict[int, int] = {}
            for txn in due_txns:
                by_user[txn.user_id] = by_user.get(txn.user_id, 0) + txn.remaining_amount
                txn.reminded_at = now
                reminded_count += 1

            await db.commit()
        except Exception as e:
            await db.rollback()
            print(f"點數到期提醒排程執行失敗: {e}")
            return

    # 標記已 commit 完才發推播，比照既有慣例（撈資料/寫DB → 關閉交易 → 再發推播）
    for user_id, total_amount in by_user.items():
        try:
            await send_user_push_notifications(
                AsyncSessionLocal,
                user_id,
                "mynotification",
                "⏰ 點數即將到期",
                f"您有 {total_amount} 點將在 {POINTS_EXPIRY_REMINDER_WINDOW_DAYS} 天內到期，記得使用！",
                {"type": "points_expiring_soon", "screen": "Points"},
            )
            notified_users += 1
        except Exception as e:
            print(f"點數到期提醒推播失敗 user_id={user_id}: {e}")

    print(
        f"[{datetime.now()}] 點數到期提醒排程完成，標記 {reminded_count} 筆 earn 紀錄、"
        f"通知 {notified_users} 位使用者"
    )


def run_points_expiry_reminder_bridge():
    """橋接 BackgroundScheduler (同步) 與 send_points_expiry_reminder_task"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(send_points_expiry_reminder_task())
    loop.close()


def run_self_ping_bridge():
    """橋接 BackgroundScheduler (同步) 與 self_ping_task"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(self_ping_task())
    loop.close()


# 初始化排程器；明確指定 timezone=Asia/Taipei（見上方 SCHEDULER_TIMEZONE 說明），
# 讓所有 cron 的 hour/day 不論部署在哪個平台都固定對應台灣當地時間
scheduler = BackgroundScheduler(timezone=SCHEDULER_TIMEZONE)


def start_scheduler():
    # 1. 正式任務：每月 25 日的台灣當地時間 00:00 執行
    scheduler.add_job(run_scheduler_bridge, "cron", day=25, hour=0, minute=0)

    # 2. 測試任務：將前面的 # 移除，使其生效
    # 設定為 interval 模式，每 1 分鐘執行一次
    # scheduler.add_job(run_scheduler_bridge, "interval", minutes=1)

    # 3. 保活任務：Render 免費方案閒置 15 分鐘會休眠，間隔設在略短於 15 分鐘
    # （14 分鐘），讓服務在有人使用的期間不會冷啟動，又不會完全 24 小時佔滿額度
    scheduler.add_job(run_self_ping_bridge, "interval", minutes=14)

    # 4. 紅利點數到期排程：每天台灣當地時間凌晨 1 點收回已過期、還沒被折抵消耗完的點數
    scheduler.add_job(run_loyalty_expiry_bridge, "cron", hour=1, minute=0)

    # 5. 點數即將到期提醒排程：每天台灣當地時間上午 10 點，跟上面的到期排程錯開、
    # 也不會太早吵到使用者手機（這是真的會推播的通知，跟上面的靜默收回不同）
    scheduler.add_job(run_points_expiry_reminder_bridge, "cron", hour=10, minute=0)

    # 啟動排程器
    scheduler.start()
    print(
        "APScheduler 已啟動（timezone=Asia/Taipei）：正式任務 (每月25日 00:00)、"
        "保活任務 (每14分鐘)、點數到期排程 (每日01:00)、"
        "點數到期提醒排程 (每日10:00) 運行中..."
    )
