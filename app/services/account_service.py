"""
帳號刪除的共用邏輯（2026-11）：App 內的 DELETE /users/me 與網頁申請的
POST /account-deletion/confirm 都呼叫這裡，確保兩邊刪除／匿名化的範圍完全一致。
設計說明見 users.py 的 delete_user_me docstring 與 CLAUDE.md「App 內刪除帳號」一節。
不自行 commit，交易邊界由呼叫端控制；店員帳號的 403 也由呼叫端檢查。
"""
import secrets

from sqlalchemy import delete, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.core.security import pwd_context


async def delete_user_account(db: AsyncSession, user: models.User) -> None:
    user_id = user.id
    old_email = user.email

    await db.execute(delete(models.PushToken).where(models.PushToken.user_id == user_id))
    # 2026-11 登入自動續期：續期證全部刪除，刪帳號後不能再換證
    await db.execute(delete(models.RefreshToken).where(models.RefreshToken.user_id == user_id))
    await db.execute(delete(models.NotificationLog).where(models.NotificationLog.user_id == user_id))
    await db.execute(delete(models.Favorite).where(models.Favorite.user_id == user_id))
    await db.execute(
        delete(models.Friendship).where(
            or_(models.Friendship.user_a_id == user_id, models.Friendship.user_b_id == user_id)
        )
    )
    # 只刪未使用的優惠券/核銷碼；已核銷的維持不動（跟 Order 一樣算交易紀錄）
    await db.execute(
        delete(models.Coupon).where(
            models.Coupon.user_id == user_id, models.Coupon.is_used.is_(False)
        )
    )
    await db.execute(
        delete(models.LoyaltyTransaction).where(models.LoyaltyTransaction.user_id == user_id)
    )
    # 2026-11 每日簽到紀錄：純會員活動紀錄，跟點數明細一樣不需保留
    await db.execute(delete(models.DailyCheckIn).where(models.DailyCheckIn.user_id == user_id))
    # 2026-11 推薦好友：被刪那方（推薦人或被推薦人）的推薦紀錄一併刪除；推薦碼在下面清空
    await db.execute(
        delete(models.Referral).where(
            or_(models.Referral.referrer_id == user_id, models.Referral.referee_id == user_id)
        )
    )
    if old_email:
        await db.execute(delete(models.MagicLinkToken).where(models.MagicLinkToken.email == old_email))
        await db.execute(
            delete(models.AccountDeletionToken).where(
                models.AccountDeletionToken.email == old_email.lower()
            )
        )
    # 2026-11 電子發票載具：訂單本身保留，但訂單上的載具快照清空——手機條碼在財政部
    # 對應到真實手機號碼，留著等於能把已匿名化的帳號連回本人；而且這期還沒真正
    # 開發票，快照沒有用途。之後串接開發票時，已開立發票的載具由發票紀錄保存，要重新評估
    for order_model in (models.Order, models.DineInOrder, models.StoreCheckout):
        await db.execute(
            update(order_model)
            .where(order_model.user_id == user_id, order_model.einvoice_carrier.isnot(None))
            .values(einvoice_carrier=None)
        )

    user.email = f"deleted-{user_id}@deleted.invalid"
    user.hashed_password = pwd_context.hash(secrets.token_urlsafe(32))
    user.nickname = None
    user.referral_code = None
    user.einvoice_carrier = None
    user.birthday = None
    user.avatar_url = None
    user.google_id = None
    user.line_id = None
    user.member_code_hash = None
    user.member_code_expires_at = None
    user.loyalty_balance = 0
    user.is_active = False
