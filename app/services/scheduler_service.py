import asyncio
from datetime import date, datetime, timedelta

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import extract, select

from app.core.config import settings
from app.database_async import AsyncSessionLocal  #
from app.models import Coupon, User  #
from app.services.push_service import send_user_push_notifications


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


def run_self_ping_bridge():
    """橋接 BackgroundScheduler (同步) 與 self_ping_task"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(self_ping_task())
    loop.close()


# 初始化排程器
scheduler = BackgroundScheduler()


def start_scheduler():
    # 1. 正式任務：每月 25 日執行 (保持原樣，這不會影響測試)
    scheduler.add_job(run_scheduler_bridge, "cron", day=25, hour=0, minute=0)

    # 2. 測試任務：將前面的 # 移除，使其生效
    # 設定為 interval 模式，每 1 分鐘執行一次
    # scheduler.add_job(run_scheduler_bridge, "interval", minutes=1)

    # 3. 保活任務：Render 免費方案閒置 15 分鐘會休眠，間隔設在略短於 15 分鐘
    # （14 分鐘），讓服務在有人使用的期間不會冷啟動，又不會完全 24 小時佔滿額度
    scheduler.add_job(run_self_ping_bridge, "interval", minutes=14)

    # 啟動排程器
    scheduler.start()
    print("APScheduler 已啟動：正式任務 (每月25日)、保活任務 (每14分鐘) 運行中...")
