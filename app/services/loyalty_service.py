# app/services/loyalty_service.py
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DineInOrder, LoyaltyTransaction, Order, StoreCheckout, User
from app.database_async import AsyncSessionLocal
from app.services import referral_service
from app.services.coupon_service import build_coupon
from app.services.push_service import send_user_push_notifications

# 以下都是先卡位用的預設常數，之後如需調整比例，直接改這裡即可（不涉及 schema/migration）：
# 消費 NT$100 得 1 點（無條件捨去）
EARN_RATE_AMOUNT_PER_POINT = Decimal("100")
# 折抵：1 點 = NT$1
REDEEM_POINT_VALUE = Decimal("1")
# 單筆訂單最高可用點數折抵訂單金額的比例
MAX_REDEEM_RATIO = Decimal("0.5")
# 每筆賺得的點數，自入帳日起幾天後過期
EARN_EXPIRY_DAYS = 365

# 會產生「一批有效期限的點數」的明細 type：有 remaining_amount／expires_at，
# 會被折抵/兌換依到期日 FIFO 扣、到期收回、到期前提醒。2026-11 每日簽到的
# check_in 跟消費回饋的 earn 一樣是這種；三處查詢（redeem_points、到期收回、
# 到期提醒）都用這個常數，新增同類 type 時加在這裡即可。
POINT_LOT_TYPES = ("earn", "check_in")

# 2026-11 會員等級：門檻依自然年度（台灣時間）累計實付消費計算
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
TIER_RANK = {"regular": 0, "silver": 1, "gold": 2}
TIER_LABEL = {"regular": "一般會員", "silver": "銀卡", "gold": "金卡"}
TIER_THRESHOLDS = {"silver": Decimal("10000"), "gold": Decimal("30000")}
EARN_MULTIPLIER = {"regular": Decimal("1"), "silver": Decimal("1.5"), "gold": Decimal("2")}
# 升等禮券金額／效期——一次跨兩級只發目標等級那張（見 process_tier_earn）
TIER_UPGRADE_COUPON_AMOUNT = {"silver": 100.0, "gold": 200.0}
TIER_UPGRADE_COUPON_VALID_DAYS = 30

# 2026-11 點數兌換優惠券：目錄寫死在這裡（plan-points-exchange.md，使用者 2026-11-07 定案），
# 前端以 GET /loyalty/exchange-options 回傳為準、不寫死。key 是對外的 option_id，不能隨意改名。
EXCHANGE_OPTIONS = {
    "coupon_100": {"title": "$100 折價券", "points_cost": 90, "discount_amount": 100},
    "coupon_300": {"title": "$300 折價券", "points_cost": 250, "discount_amount": 300},
    "coupon_500": {"title": "$500 折價券", "points_cost": 400, "discount_amount": 500},
}
EXCHANGE_COUPON_VALID_DAYS = 30
# 每人每月（台灣時間自然月）最多兌換張數，三種合計
EXCHANGE_MONTHLY_LIMIT = 3


def calc_earned_points(order_total: Decimal, multiplier: Decimal = Decimal("1")) -> int:
    """
    依訂單實付金額換算可得點數：floor(實付金額 ÷ 100 × 倍率)——除法跟乘法要一起做
    完才取floor，不能先 floor(金額÷100) 再乘倍率，兩者在倍率非整數時（例如銀卡
    1.5 倍）結果不一樣。multiplier 預設 1，不帶就是舊有的「無等級」行為。
    """
    return int(order_total / EARN_RATE_AMOUNT_PER_POINT * multiplier)


def calc_max_redeemable_points(order_subtotal: Decimal) -> int:
    """該筆訂單最多可折抵的點數（訂單小計 * 50% 換算成點數，無條件捨去）。"""
    return int((order_subtotal * MAX_REDEEM_RATIO) // REDEEM_POINT_VALUE)


def _tier_from_spend(spend: Decimal) -> str:
    if spend >= TIER_THRESHOLDS["gold"]:
        return "gold"
    if spend >= TIER_THRESHOLDS["silver"]:
        return "silver"
    return "regular"


def _year_bounds(year: int) -> tuple[datetime, datetime]:
    """回傳該年度（台灣時間 1/1 00:00 ～ 次年 1/1 00:00）的起訖時間，皆含時區。"""
    start = datetime(year, 1, 1, tzinfo=TAIPEI_TZ)
    end = datetime(year + 1, 1, 1, tzinfo=TAIPEI_TZ)
    return start, end


async def _spend_in_range(db: AsyncSession, user_id: int, start: datetime, end: datetime) -> Decimal:
    """
    計入範圍跟賺點數一樣：已付款的網購訂單（用 paid_at 判斷，自動涵蓋後續轉
    shipped 的訂單，因為 paid_at 不會被出貨清掉）、已完成的堂食（沒有獨立的
    「完成時間」欄位，用 created_at 當近似值）、會員快速結帳（創建即完成，
    created_at 就是完成時間）。取消的不算——cancelled 的 Order/DineInOrder
    本來就不會落在這些條件內（cancelled 的 Order 沒有 paid_at；DineInOrder
    若是從 served 取消，status 也不會是 completed）。
    """
    order_sum = (
        await db.execute(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.user_id == user_id,
                Order.paid_at.isnot(None),
                Order.paid_at >= start,
                Order.paid_at < end,
            )
        )
    ).scalar_one()
    dine_in_sum = (
        await db.execute(
            select(func.coalesce(func.sum(DineInOrder.total_amount), 0)).where(
                DineInOrder.user_id == user_id,
                DineInOrder.status == "completed",
                DineInOrder.created_at >= start,
                DineInOrder.created_at < end,
            )
        )
    ).scalar_one()
    store_sum = (
        await db.execute(
            select(func.coalesce(func.sum(StoreCheckout.total_amount), 0)).where(
                StoreCheckout.user_id == user_id,
                StoreCheckout.created_at >= start,
                StoreCheckout.created_at < end,
            )
        )
    ).scalar_one()
    return Decimal(order_sum) + Decimal(dine_in_sum) + Decimal(store_sum)


async def get_member_tier_info(db: AsyncSession, user_id: int, as_of: datetime | None = None) -> dict:
    """
    即時計算會員等級資訊，給 GET /loyalty/me 用，不需要年底結算排程。
    tier = max(去年全年消費對應等級, 今年至今消費對應等級)（取 max 時直接拿兩個
    消費金額的較大值換算等級即可，因為 _tier_from_spend 是單調遞增的 step
    function，max(tier(a), tier(b)) == tier(max(a, b))，不用算兩次再比大小）。
    """
    now = as_of or datetime.now(UTC)
    this_year = now.astimezone(TAIPEI_TZ).year
    this_year_start, this_year_end = _year_bounds(this_year)
    last_year_start, last_year_end = _year_bounds(this_year - 1)

    year_spend = await _spend_in_range(db, user_id, this_year_start, this_year_end)
    last_year_spend = await _spend_in_range(db, user_id, last_year_start, last_year_end)

    this_year_tier = _tier_from_spend(year_spend)
    last_year_tier = _tier_from_spend(last_year_spend)
    tier = this_year_tier if TIER_RANK[this_year_tier] >= TIER_RANK[last_year_tier] else last_year_tier

    if tier == "regular":
        tier_expires_at = None
    elif TIER_RANK[this_year_tier] >= TIER_RANK[last_year_tier]:
        # 今年自己的消費撐得起這個等級：有效到明年底
        tier_expires_at = date(this_year + 1, 12, 31)
    else:
        # 只靠去年消費撐著（今年還沒追上）：有效到今年底
        tier_expires_at = date(this_year, 12, 31)

    if tier == "gold":
        next_tier = None
        amount_to_next_tier = None
    else:
        next_tier = "silver" if tier == "regular" else "gold"
        amount_to_next_tier = int(TIER_THRESHOLDS[next_tier] - year_spend)

    return {
        "tier": tier,
        "year_spend": int(year_spend),
        "next_tier": next_tier,
        "amount_to_next_tier": amount_to_next_tier,
        "tier_expires_at": tier_expires_at,
        "earn_multiplier": float(EARN_MULTIPLIER[tier]),
    }


async def process_tier_earn(
    db: AsyncSession,
    user_id: int,
    amount_paid: Decimal,
    reason_prefix: str,
    related_order_id: int | None = None,
    related_dine_in_order_id: int | None = None,
    related_store_checkout_id: int | None = None,
    restaurant_id: int | None = None,
) -> dict:
    """
    三個賺點觸發點（ECPay callback／堂食標記完成／會員快速結帳）共用的入口：
    算出「這筆消費完成前」的等級對應倍率、入帳點數（reason 帶上倍率說明），
    再用「消費完成前的等級 vs 消費完成後的等級」判斷這筆消費有沒有讓使用者
    跨過新的等級門檻，有的話建立升等禮券。

    用 SELECT ... FOR UPDATE 鎖 User 這一列，序列化同一人同時有多筆消費在算
    等級的情況，避免兩筆消費平行讀到同一個「消費前」基準而各自誤判成跨等級、
    重複發兩次禮券。

    「每個等級每年度最多發一次」不需要額外的紀錄表去重——年度累計消費在一年
    內只會單調遞增（已完成的 Order/DineInOrder/StoreCheckout 都沒有「取消」
    或「退款」回去的路徑，見 _spend_in_range 的說明），所以同一年內「消費後
    等級 > 消費前等級」這個跨越事件对每個等級最多只會發生一次；真的一次跨兩級
    （例如一筆大額消費直接從一般衝到金卡）也只會比較「消費前」跟「消費後」
    兩個等級，只發消費後那個等級（金卡）的禮券，不會把銀卡也一起發。

    不會自己 commit（跟 earn_points 等既有函式一致），交易邊界由呼叫端控制；
    但呼叫端commit 之後才能把回傳的 coupon_id／upgraded_to 拿去推播，因為推播
    （BackgroundTasks）照既有慣例是在 endpoint 層觸發，不是在 service 層。

    回傳 {"earned_points": int, "upgraded_to": "silver"|"gold"|None, "coupon_id": int|None}。
    """
    now = datetime.now(UTC)
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())

    # 呼叫端在呼叫這裡之前，已經把這筆消費標成「已計入」（Order.paid_at／
    # DineInOrder.status="completed"／StoreCheckout 已 add）。session 是
    # autoflush=False，有的呼叫端有 flush、有的沒有，查出來的累計金額會不一致；
    # 這裡統一先 flush，讓查詢一定包含這筆，再扣掉 amount_paid 得到「消費前」。
    await db.flush()
    this_year = now.astimezone(TAIPEI_TZ).year
    this_year_start, this_year_end = _year_bounds(this_year)
    last_year_start, last_year_end = _year_bounds(this_year - 1)
    year_spend_after = await _spend_in_range(db, user_id, this_year_start, this_year_end)
    year_spend_before = year_spend_after - amount_paid
    last_year_spend = await _spend_in_range(db, user_id, last_year_start, last_year_end)

    tier_before = _tier_from_spend(max(year_spend_before, last_year_spend))
    multiplier = EARN_MULTIPLIER[tier_before]
    earned_points = calc_earned_points(amount_paid, multiplier)

    if earned_points > 0:
        reason = f"{reason_prefix}（{TIER_LABEL[tier_before]} {multiplier} 倍）"
        await earn_points(
            db,
            user_id,
            earned_points,
            reason=reason,
            related_order_id=related_order_id,
            related_dine_in_order_id=related_dine_in_order_id,
            related_store_checkout_id=related_store_checkout_id,
            restaurant_id=restaurant_id,
        )

    tier_after = _tier_from_spend(max(year_spend_after, last_year_spend))
    upgraded_to = None
    coupon_id = None
    if TIER_RANK[tier_after] > TIER_RANK[tier_before]:
        upgraded_to = tier_after
        # 升等禮券是連鎖層級的會員獎勵（同新會員／生日禮券），不標記門市
        coupon = build_coupon(
            user_id=user_id,
            title=f"升等{TIER_LABEL[tier_after]}禮券",
            discount_amount=TIER_UPGRADE_COUPON_AMOUNT[tier_after],
            valid_days=TIER_UPGRADE_COUPON_VALID_DAYS,
        )
        db.add(coupon)
        await db.flush()
        coupon_id = coupon.id

    # 2026-11 推薦好友：被推薦的新會員第一筆消費完成 → 推薦人拿券（沒被推薦或不是首購回 None）
    referral = await referral_service.process_first_purchase(db, user_id)

    return {
        "earned_points": earned_points,
        "upgraded_to": upgraded_to,
        "coupon_id": coupon_id,
        "referral": referral,
    }


def queue_reward_pushes(background_tasks, user_id: int, result: dict | None) -> None:
    """
    process_tier_earn 的結果在呼叫端 commit 之後，用這支排推播（三個賺點觸發點共用）：
    升等推播給消費者本人、推薦獎勵推播給推薦人。result 是 None（例如付款失敗）就不做事。
    """
    if not result:
        return
    if result["upgraded_to"]:
        tier = result["upgraded_to"]
        title, body = tier_upgrade_push_text(tier)
        background_tasks.add_task(
            send_user_push_notifications,
            AsyncSessionLocal,
            user_id,
            "mynotification",
            title,
            body,
            tier_upgrade_push_data(tier, result["coupon_id"]),
        )
    if result.get("referral"):
        ref = result["referral"]
        background_tasks.add_task(
            send_user_push_notifications,
            AsyncSessionLocal,
            ref["referrer_id"],
            "mynotification",
            "推薦好友成功！",
            f"您推薦的好友完成第一筆消費，送您一張 ${int(referral_service.REFERRER_COUPON_AMOUNT)} 推薦好友禮券",
            {"type": "referral_rewarded", "screen": "Coupons", "coupon_id": ref["coupon_id"]},
        )


def tier_upgrade_push_text(tier: str) -> tuple[str, str]:
    """升等推播的 title/body，三個呼叫端共用，確保文案一致。"""
    amount = int(TIER_UPGRADE_COUPON_AMOUNT[tier])
    multiplier = EARN_MULTIPLIER[tier]
    title = f"恭喜升等{TIER_LABEL[tier]}！"
    body = f"送您一張 ${amount} 升等禮券，之後消費點數 {multiplier} 倍回饋"
    return title, body


def tier_upgrade_push_data(tier: str, coupon_id: int) -> dict:
    """升等推播 data payload（跟 mynotification 對過的契約，不能隨意改）。"""
    return {"type": "tier_upgraded", "screen": "Coupons", "coupon_id": coupon_id, "tier": tier}


async def get_tier_for_user(db: AsyncSession, user_id: int) -> str:
    """只要等級字串時用（生日禮券、店員查會員碼）。"""
    return (await get_member_tier_info(db, user_id))["tier"]


async def earned_points_by_related(db: AsyncSession, column, ids: list[int]) -> dict[int, int]:
    """
    一次查出多筆訂單「實際入帳」的消費回饋點數（type="earn" 加總），給訂單回應的
    points_earned 用。有等級倍率之後不能再用 calc_earned_points(total) 重算——
    倍率取決於當時的等級，重算會跟 LoyaltyTransaction 對不上。
    column 傳 LoyaltyTransaction.related_order_id 之類的欄位。
    """
    if not ids:
        return {}
    result = await db.execute(
        select(column, func.sum(LoyaltyTransaction.amount))
        .where(LoyaltyTransaction.type == "earn", column.in_(ids))
        .group_by(column)
    )
    return {related_id: int(total) for related_id, total in result.all()}


async def earn_points(
    db: AsyncSession,
    user_id: int,
    amount: int,
    reason: str,
    related_order_id: int | None = None,
    related_dine_in_order_id: int | None = None,
    related_store_checkout_id: int | None = None,
    restaurant_id: int | None = None,
    txn_type: str = "earn",
) -> None:
    """
    txn_type 預設 "earn"（消費回饋）；每日簽到傳 "check_in"，兩者都是 POINT_LOT_TYPES，
    入帳方式完全相同，只是明細 type 分開記。

    記一筆賺點：新增 type="earn" 的 LoyaltyTransaction（remaining_amount 初始等於
    amount，expires_at 為入帳日 + EARN_EXPIRY_DAYS 天），並原子性增加 User.loyalty_balance。
    不會自己 commit，交易邊界由呼叫端控制（方便跟訂單建立/狀態更新包在同一個 transaction）。

    restaurant_id 純粹是記錄用途（2026-09 多門市支援：統一錢包＋記錄消費門市，
    不是各門市獨立錢包）——不影響餘額計算，堂食訂單以外的呼叫端（網購、連鎖層級
    禮券）沒有門市脈絡就留 None。
    """
    if amount <= 0:
        return

    db.add(
        LoyaltyTransaction(
            user_id=user_id,
            type=txn_type,
            amount=amount,
            remaining_amount=amount,
            expires_at=datetime.now(UTC) + timedelta(days=EARN_EXPIRY_DAYS),
            reason=reason,
            related_order_id=related_order_id,
            related_dine_in_order_id=related_dine_in_order_id,
            related_store_checkout_id=related_store_checkout_id,
            restaurant_id=restaurant_id,
        )
    )
    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(loyalty_balance=User.loyalty_balance + amount)
    )


async def redeem_points(
    db: AsyncSession,
    user_id: int,
    amount: int,
    reason: str,
    related_order_id: int | None = None,
    related_dine_in_order_id: int | None = None,
    related_store_checkout_id: int | None = None,
    restaurant_id: int | None = None,
    txn_type: str = "redeem",
    related_coupon_id: int | None = None,
) -> bool:
    """
    折抵點數：先用原子性 UPDATE 扣減 User.loyalty_balance（餘額不足回傳 False，不寫入
    任何紀錄，由呼叫端決定要回什麼錯誤）；成功後依 expires_at 由舊到新，依序從尚有
    remaining_amount 的 earn 列扣除（FIFO），最後補一筆交易紀錄。
    不會自己 commit，交易邊界由呼叫端控制。

    txn_type 預設 "redeem"（訂單折抵）；點數兌換優惠券傳 "exchange"——兩者扣點方式
    完全相同，只是明細 type 分開記，避免訂單取消退點時誤把兌換的點數也當成折抵退回。
    """
    if amount <= 0:
        return True

    statement = (
        update(User)
        .where(User.id == user_id)
        .where(User.loyalty_balance >= amount)
        .values(loyalty_balance=User.loyalty_balance - amount)
    )
    result = await db.execute(statement)
    if result.rowcount == 0:
        return False

    remaining_to_consume = amount
    query = (
        select(LoyaltyTransaction)
        .where(
            LoyaltyTransaction.user_id == user_id,
            LoyaltyTransaction.type.in_(POINT_LOT_TYPES),
            LoyaltyTransaction.remaining_amount > 0,
        )
        .order_by(LoyaltyTransaction.expires_at.asc())
    )
    earn_result = await db.execute(query)
    for earn_txn in earn_result.scalars().all():
        if remaining_to_consume <= 0:
            break
        consume = min(earn_txn.remaining_amount, remaining_to_consume)
        earn_txn.remaining_amount -= consume
        remaining_to_consume -= consume

    db.add(
        LoyaltyTransaction(
            user_id=user_id,
            type=txn_type,
            amount=amount,
            reason=reason,
            related_order_id=related_order_id,
            related_dine_in_order_id=related_dine_in_order_id,
            related_store_checkout_id=related_store_checkout_id,
            restaurant_id=restaurant_id,
            related_coupon_id=related_coupon_id,
        )
    )
    return True


def _month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """回傳 now 所在月份（台灣時間）的起訖時間，皆含時區。"""
    local = now.astimezone(TAIPEI_TZ)
    start = datetime(local.year, local.month, 1, tzinfo=TAIPEI_TZ)
    if local.month == 12:
        end = datetime(local.year + 1, 1, 1, tzinfo=TAIPEI_TZ)
    else:
        end = datetime(local.year, local.month + 1, 1, tzinfo=TAIPEI_TZ)
    return start, end


async def count_exchanges_this_month(db: AsyncSession, user_id: int) -> int:
    """本月（台灣時間自然月）已兌換張數，直接數 type="exchange" 的明細，不另開紀錄表。"""
    start, end = _month_bounds(datetime.now(UTC))
    result = await db.execute(
        select(func.count()).where(
            LoyaltyTransaction.user_id == user_id,
            LoyaltyTransaction.type == "exchange",
            LoyaltyTransaction.created_at >= start,
            LoyaltyTransaction.created_at < end,
        )
    )
    return result.scalar_one()


async def exchange_points_for_coupon(db: AsyncSession, user_id: int, option_id: str):
    """
    點數兌換優惠券。option_id 必須是 EXCHANGE_OPTIONS 裡的 key（呼叫端先擋 404）。
    先 SELECT ... FOR UPDATE 鎖住 User 列，序列化同一人連點的兩個請求，
    再檢查月上限 → 建券（flush 拿 id）→ 扣點（type="exchange"，FIFO 同折抵）。
    不會自己 commit；回傳 "limit"／"insufficient" 時呼叫端要 rollback（券才不會留下）。

    回傳 (outcome, coupon)：("ok", Coupon)／("limit", None)／("insufficient", None)。
    """
    option = EXCHANGE_OPTIONS[option_id]
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())

    if await count_exchanges_this_month(db, user_id) >= EXCHANGE_MONTHLY_LIMIT:
        return "limit", None

    coupon = build_coupon(
        user_id=user_id,
        title=option["title"],
        discount_amount=float(option["discount_amount"]),
        valid_days=EXCHANGE_COUPON_VALID_DAYS,
    )
    db.add(coupon)
    await db.flush()

    redeemed = await redeem_points(
        db,
        user_id,
        option["points_cost"],
        reason=f"兌換 {option['title']}",
        txn_type="exchange",
        related_coupon_id=coupon.id,
    )
    if not redeemed:
        return "insufficient", None
    return "ok", coupon


async def reverse_redeem_points(
    db: AsyncSession,
    user_id: int,
    amount: int,
    reason: str,
    related_order_id: int | None = None,
    related_dine_in_order_id: int | None = None,
    related_store_checkout_id: int | None = None,
    restaurant_id: int | None = None,
) -> None:
    """
    退還一筆先前的折抵（訂單建立時扣了點數，但付款沒有成功／使用者取消訂單）：
    原子性加回 User.loyalty_balance，並補一筆 type="reverse_redeem" 的交易紀錄，
    跟 earn/redeem/expire 共用「type 決定異動方向」的不變量（見 CLAUDE.md 紅利點數一節）。
    不會自己 commit，交易邊界由呼叫端控制。

    已知簡化：不會反向補回原本 redeem 時依 FIFO 扣減的各筆 earn 紀錄的 remaining_amount，
    所以這筆退還的點數之後不會被 expire_loyalty_points_task 收回（永遠不過期）。
    對使用者是偏寬鬆而非有安全疑慮的方向，暫不處理；真的要精確重建 remaining_amount
    需要在 redeem_points 當下記錄「這次扣了哪幾筆 earn 各多少」，目前沒有這個資料。
    """
    if amount <= 0:
        return

    await db.execute(
        update(User)
        .where(User.id == user_id)
        .values(loyalty_balance=User.loyalty_balance + amount)
    )
    db.add(
        LoyaltyTransaction(
            user_id=user_id,
            type="reverse_redeem",
            amount=amount,
            reason=reason,
            related_order_id=related_order_id,
            related_dine_in_order_id=related_dine_in_order_id,
            related_store_checkout_id=related_store_checkout_id,
            restaurant_id=restaurant_id,
        )
    )
