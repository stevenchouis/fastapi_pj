from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Coupon

DEFAULT_COUPON_VALID_DAYS = 30


def build_coupon(
    user_id: int,
    title: str,
    discount_amount: float,
    valid_days: int = DEFAULT_COUPON_VALID_DAYS,
    restaurant_id: int | None = None,
) -> Coupon:
    """建立一張「N 天後到期」的優惠券物件（尚未 add/commit）。
    給管理者手動發券、新會員歡迎禮券共用。
    生日禮券的到期日是「下個月最後一天」，計算方式本質不同，不套用這個 helper。

    restaurant_id 純記錄用途（2026-09 多門市支援），不給就是連鎖層級（新會員
    歡迎禮券固定不傳，維持連鎖層級），不影響核銷資格。"""
    return Coupon(
        user_id=user_id,
        title=title,
        discount_amount=discount_amount,
        expired_at=datetime.now(UTC) + timedelta(days=valid_days),
        is_used=False,
        restaurant_id=restaurant_id,
    )


async def apply_coupon_for_checkout(
    db: AsyncSession,
    user_id: int,
    coupon_id: int,
) -> tuple[str, Decimal | None]:
    """
    網購／堂食結帳時直接折抵一張優惠券——跟到店核銷（產生 10 分鐘核銷碼給店員掃）
    是不同通路，這裡不產生核銷碼，驗證通過就直接原子性標記為已使用。做法比照
    loyalty_service.redeem_points：不會自己 commit，交易邊界由呼叫端控制，失敗時
    呼叫端自行 rollback。

    兩個通路不會雙重折抵：不管是這裡還是 coupons.py 的 /redeem，最終都是靠同一個
    Coupon.is_used 欄位的原子性 conditional UPDATE 才能成功，誰先達成、is_used
    一變 True，另一條路的 UPDATE 就會抓不到 row 而自然失敗。

    回傳 (outcome, discount_amount)：
    - ("ok", discount_amount)：驗證通過且已標記為已使用，discount_amount 是券面額
      （呼叫端需自行 clamp 到不超過訂單小計）
    - ("not_found", None)：優惠券不存在或不屬於此使用者
    - ("already_used", None)：已被使用（含到店核銷、或極端情況下被另一個請求搶先）
    - ("expired", None)：已過期
    """
    now = datetime.now(UTC)
    query = select(Coupon).where(Coupon.id == coupon_id, Coupon.user_id == user_id)
    result = await db.execute(query)
    coupon = result.scalars().first()
    if coupon is None:
        return "not_found", None
    if coupon.is_used:
        return "already_used", None
    if coupon.expired_at < now:
        return "expired", None

    statement = (
        update(Coupon)
        .where(Coupon.id == coupon_id)
        .where(Coupon.is_used.is_(False))
        .where(Coupon.expired_at > now)
        .values(is_used=True, used_at=now)
        .returning(Coupon.discount_amount)
    )
    result = await db.execute(statement)
    row = result.first()
    if row is None:
        # 極端 race：跟前面的 SELECT 之間被另一個請求（例如店員掃碼核銷）搶先
        return "already_used", None
    (discount_amount,) = row
    return "ok", Decimal(str(discount_amount))


async def release_coupon(db: AsyncSession, coupon_id: int) -> None:
    """
    退還一張先前用 apply_coupon_for_checkout 標記為已使用的優惠券（訂單付款沒有成功、
    或使用者主動取消訂單）：改回 is_used=False、清掉 used_at，讓它可以再被使用一次。
    不會自己 commit，交易邊界由呼叫端控制。只有這張券真的是「已使用」狀態才會改動
    （避免誤把一張本來就沒被這筆訂單用掉、或已被其他方式核銷的券狀態弄亂）。
    """
    await db.execute(
        update(Coupon)
        .where(Coupon.id == coupon_id)
        .where(Coupon.is_used.is_(True))
        .values(is_used=False, used_at=None)
    )
