from datetime import UTC, datetime, timedelta

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
