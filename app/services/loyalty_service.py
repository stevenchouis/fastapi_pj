# app/services/loyalty_service.py
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LoyaltyTransaction, User

# 以下都是先卡位用的預設常數，之後如需調整比例，直接改這裡即可（不涉及 schema/migration）：
# 消費 NT$100 得 1 點（無條件捨去）
EARN_RATE_AMOUNT_PER_POINT = Decimal("100")
# 折抵：1 點 = NT$1
REDEEM_POINT_VALUE = Decimal("1")
# 單筆訂單最高可用點數折抵訂單金額的比例
MAX_REDEEM_RATIO = Decimal("0.5")
# 每筆賺得的點數，自入帳日起幾天後過期
EARN_EXPIRY_DAYS = 365


def calc_earned_points(order_total: Decimal) -> int:
    """依訂單實付金額換算可得點數，無條件捨去。"""
    return int(order_total // EARN_RATE_AMOUNT_PER_POINT)


def calc_max_redeemable_points(order_subtotal: Decimal) -> int:
    """該筆訂單最多可折抵的點數（訂單小計 * 50% 換算成點數，無條件捨去）。"""
    return int((order_subtotal * MAX_REDEEM_RATIO) // REDEEM_POINT_VALUE)


async def earn_points(
    db: AsyncSession,
    user_id: int,
    amount: int,
    reason: str,
    related_order_id: int | None = None,
    related_dine_in_order_id: int | None = None,
) -> None:
    """
    記一筆賺點：新增 type="earn" 的 LoyaltyTransaction（remaining_amount 初始等於
    amount，expires_at 為入帳日 + EARN_EXPIRY_DAYS 天），並原子性增加 User.loyalty_balance。
    不會自己 commit，交易邊界由呼叫端控制（方便跟訂單建立/狀態更新包在同一個 transaction）。
    """
    if amount <= 0:
        return

    db.add(
        LoyaltyTransaction(
            user_id=user_id,
            type="earn",
            amount=amount,
            remaining_amount=amount,
            expires_at=datetime.now(UTC) + timedelta(days=EARN_EXPIRY_DAYS),
            reason=reason,
            related_order_id=related_order_id,
            related_dine_in_order_id=related_dine_in_order_id,
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
) -> bool:
    """
    折抵點數：先用原子性 UPDATE 扣減 User.loyalty_balance（餘額不足回傳 False，不寫入
    任何紀錄，由呼叫端決定要回什麼錯誤）；成功後依 expires_at 由舊到新，依序從尚有
    remaining_amount 的 earn 列扣除（FIFO），最後補一筆 type="redeem" 的交易紀錄。
    不會自己 commit，交易邊界由呼叫端控制。
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
            LoyaltyTransaction.type == "earn",
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
            type="redeem",
            amount=amount,
            reason=reason,
            related_order_id=related_order_id,
            related_dine_in_order_id=related_dine_in_order_id,
        )
    )
    return True
