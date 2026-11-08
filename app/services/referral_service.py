# app/services/referral_service.py
"""
2026-11 推薦好友（mynotification plan-referral.md，使用者 2026-11-08 定案）。

- 每位會員一組固定 6 碼推薦碼（大寫英數、去掉 0/O/1/I），輸入不分大小寫。
- 新會員填碼當下拿 $50 券；推薦人等新會員第一筆消費完成才拿 $100 券。券都是 30 天、
  連鎖層級（restaurant_id=None）。
- 第一筆消費完成＝三個賺點觸發點（網購 paid／堂食 completed／會員快速結帳），掛在
  loyalty_service.process_tier_earn 裡呼叫 process_first_purchase；不設最低金額；
  之後取消也不收回推薦人的券。
- 推薦人每年（台灣時間自然年）最多拿 10 次，超過仍可推薦（新會員照拿）。
- 每位新會員只能被推薦一次、不能改、不能填自己的碼；補填限註冊 7 天內、還沒消費過。

所有函式都不自己 commit，交易邊界由呼叫端控制（跟 loyalty_service 一致）。
"""
import secrets
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DineInOrder, Order, Referral, StoreCheckout, User
from app.services.coupon_service import build_coupon

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# 去掉容易看錯的 0/O/1/I，剩 32 個字元；6 碼約 10 億種組合
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 6

REFEREE_COUPON_TITLE = "好友推薦禮券"
REFEREE_COUPON_AMOUNT = 50.0
REFERRER_COUPON_TITLE = "推薦好友禮券"
REFERRER_COUPON_AMOUNT = 100.0
REFERRAL_COUPON_VALID_DAYS = 30
REFERRER_YEARLY_LIMIT = 10
APPLY_WINDOW_DAYS = 7


def normalize_code(code: str) -> str:
    return code.strip().upper()


def is_valid_code_format(code: str) -> bool:
    """格式檢查（已 normalize 過的碼）：剛好 6 碼、每個字元都在 CODE_ALPHABET 內。不查 DB。"""
    return len(code) == CODE_LENGTH and all(ch in CODE_ALPHABET for ch in code)


def _generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


async def ensure_referral_code(db: AsyncSession, user: User) -> str:
    """
    回傳使用者的推薦碼，沒有就產生一組（第一次查詢才產生，不用替既有帳號批次回填）。
    先查有沒有撞號再寫入；真的在查完到寫入之間被別人搶走同一組（機率極低），
    unique 限制會讓 flush 失敗，用 savepoint 包起來換一組重試，不影響外層交易。
    """
    if user.referral_code:
        return user.referral_code
    from sqlalchemy.exc import IntegrityError

    for _ in range(10):
        code = _generate_code()
        taken = (await db.execute(select(User.id).where(User.referral_code == code))).first()
        if taken:
            continue
        try:
            async with db.begin_nested():
                user.referral_code = code
                await db.flush()
            return code
        except IntegrityError:
            user.referral_code = None
            continue
    raise RuntimeError("推薦碼產生失敗")


def referral_display_name(user: User) -> str:
    """
    推薦關係顯示名稱：暱稱優先；沒有就遮罩 email（前 2 碼 + *** + @網域，例如
    st***@hotmail.com，plan 指定的格式，跟好友 API 的 display_name 不同、不共用）；
    純 LINE 帳號沒有 email 就用「用戶{id}」。
    """
    if user.nickname:
        return user.nickname
    if user.email:
        local, _, domain = user.email.partition("@")
        return f"{local[:2]}***@{domain}" if domain else f"{local[:2]}***"
    return f"用戶{user.id}"


async def has_consumed(db: AsyncSession, user_id: int) -> bool:
    """有沒有任何一筆「已完成的消費」——計入範圍跟賺點、會員等級的 year_spend 一樣。"""
    order_q = select(
        exists().where(Order.user_id == user_id, Order.paid_at.isnot(None))
    )
    dine_q = select(
        exists().where(DineInOrder.user_id == user_id, DineInOrder.status == "completed")
    )
    store_q = select(exists().where(StoreCheckout.user_id == user_id))
    for q in (order_q, dine_q, store_q):
        if (await db.execute(q)).scalar():
            return True
    return False


def apply_deadline(user: User) -> datetime | None:
    """
    補填期限：註冊日（台灣時間）+ 7 天的 23:59:59（例如 11/8 註冊 → 11/15 23:59:59+08:00）。
    created_at 是 NULL（推薦功能上線前就存在的舊帳號，migration 刻意不回填）→ None，
    視為期限已過。
    """
    if user.created_at is None:
        return None
    reg_date = user.created_at.astimezone(TAIPEI_TZ).date()
    return datetime.combine(
        reg_date + timedelta(days=APPLY_WINDOW_DAYS), time(23, 59, 59), tzinfo=TAIPEI_TZ
    )


async def get_referral_of(db: AsyncSession, referee_id: int) -> Referral | None:
    result = await db.execute(select(Referral).where(Referral.referee_id == referee_id))
    return result.scalars().first()


async def can_apply(db: AsyncSession, user: User) -> tuple[bool, datetime | None]:
    """回傳 (能不能補填, 補填期限)；不能補填時期限一律回 None（API 契約）。"""
    if await get_referral_of(db, user.id):
        return False, None
    deadline = apply_deadline(user)
    if deadline is None or datetime.now(UTC) > deadline:
        return False, None
    if await has_consumed(db, user.id):
        return False, None
    return True, deadline


async def apply_referral(db: AsyncSession, referee: User, code: str, check_window: bool = True):
    """
    記錄推薦關係並發 $50 券給新會員。註冊時（POST /users/register 帶 referral_code）
    傳 check_window=False——剛建立的帳號本來就在期限內、沒有消費、沒有被推薦過。

    回傳 (outcome, referrer, coupon)：outcome 是 "ok"／"invalid"／"self"／
    "already"／"window_closed"。不是 "ok" 時呼叫端要 rollback。
    兩個請求同時補填：先鎖 referee 的 User 列序列化；最後還有 referee_id 唯一限制兜底
    （IntegrityError 由呼叫端視為 already_referred）。
    """
    referrer = (
        await db.execute(
            select(User).where(
                User.referral_code == normalize_code(code), User.is_active.is_(True)
            )
        )
    ).scalars().first()
    if referrer is None:
        return "invalid", None, None
    if referrer.id == referee.id:
        return "self", None, None

    if check_window:
        await db.execute(select(User.id).where(User.id == referee.id).with_for_update())
        if await get_referral_of(db, referee.id):
            return "already", None, None
        allowed, _ = await can_apply(db, referee)
        if not allowed:
            return "window_closed", None, None

    coupon = build_coupon(
        user_id=referee.id,
        title=REFEREE_COUPON_TITLE,
        discount_amount=REFEREE_COUPON_AMOUNT,
        valid_days=REFERRAL_COUPON_VALID_DAYS,
    )
    db.add(coupon)
    await db.flush()
    db.add(Referral(referrer_id=referrer.id, referee_id=referee.id, referee_coupon_id=coupon.id))
    await db.flush()
    return "ok", referrer, coupon


def _year_bounds(now: datetime) -> tuple[datetime, datetime]:
    year = now.astimezone(TAIPEI_TZ).year
    return (
        datetime(year, 1, 1, tzinfo=TAIPEI_TZ),
        datetime(year + 1, 1, 1, tzinfo=TAIPEI_TZ),
    )


async def count_rewarded_this_year(db: AsyncSession, referrer_id: int) -> int:
    start, end = _year_bounds(datetime.now(UTC))
    result = await db.execute(
        select(func.count()).where(
            Referral.referrer_id == referrer_id,
            Referral.referrer_rewarded_at >= start,
            Referral.referrer_rewarded_at < end,
        )
    )
    return result.scalar_one()


async def process_first_purchase(db: AsyncSession, referee_id: int) -> dict | None:
    """
    新會員一筆消費完成時呼叫（loyalty_service.process_tier_earn 裡，三個賺點觸發點共用）。
    只有「有被推薦、而且還沒記過首購」的人會做事：記 first_purchase_at，推薦人今年還沒
    領滿 10 次就發 $100 券。回傳 {"referrer_id", "coupon_id"} 讓呼叫端 commit 後推播，
    其他情況回 None。

    鎖定：process_tier_earn 已經鎖住 referee 的 User 列（同一人的消費會排隊，不會兩筆
    都當成首購）；這裡再鎖推薦人的 User 列，序列化「不同新會員同時首購、都算到同一個
    推薦人」的情況，年度上限才不會被並發超過。
    """
    referral = (
        await db.execute(
            select(Referral)
            .where(Referral.referee_id == referee_id, Referral.first_purchase_at.is_(None))
            .with_for_update()
        )
    ).scalars().first()
    if referral is None:
        return None

    now = datetime.now(UTC)
    referral.first_purchase_at = now
    referrer_id = referral.referrer_id

    await db.execute(select(User.id).where(User.id == referrer_id).with_for_update())
    if await count_rewarded_this_year(db, referrer_id) >= REFERRER_YEARLY_LIMIT:
        await db.flush()
        return None

    coupon = build_coupon(
        user_id=referrer_id,
        title=REFERRER_COUPON_TITLE,
        discount_amount=REFERRER_COUPON_AMOUNT,
        valid_days=REFERRAL_COUPON_VALID_DAYS,
    )
    db.add(coupon)
    await db.flush()
    referral.referrer_coupon_id = coupon.id
    referral.referrer_rewarded_at = now
    await db.flush()
    return {"referrer_id": referrer_id, "coupon_id": coupon.id}
