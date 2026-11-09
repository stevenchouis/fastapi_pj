import logging

import httpx

from app.core.config import settings
from app.core.monitoring import capture_exception, capture_message

logger = logging.getLogger(__name__)

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


async def _send_brevo(email: str, subject: str, html: str, label: str) -> None:
    """用 Brevo HTTP API 寄信。失敗只記錄 log、不拋例外（呼叫端的回應不能洩漏寄信結果）。
    走 HTTPS，不會碰到 Render 免費方案可能擋 SMTP 連線埠的問題。"""
    if not settings.BREVO_API_KEY or not settings.MAIL_FROM_EMAIL:
        logger.error(f"BREVO_API_KEY 或 MAIL_FROM_EMAIL 尚未設定，無法寄送{label}信件")
        capture_message(f"Brevo 尚未設定，無法寄送{label}信件", area="email")
        return

    payload = {
        "sender": {"name": settings.MAIL_FROM_NAME, "email": settings.MAIL_FROM_EMAIL},
        "to": [{"email": email}],
        "subject": subject,
        "htmlContent": html,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                BREVO_SEND_URL,
                headers={
                    "api-key": settings.BREVO_API_KEY,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )
        if response.status_code in (200, 201):
            logger.info(f"{label}信件已寄出: {email}")
        else:
            logger.error(
                f"🔥 {label}信件寄送失敗 (status={response.status_code}): {response.text}"
            )
            # 只送狀態碼，不送 Brevo 回應內容（可能帶收件人 Email）
            capture_message(f"Brevo {label}信件寄送失敗 status={response.status_code}", area="email")
    except Exception as e:
        logger.error(f"🔥 {label}信件寄送失敗: {e}")
        capture_exception(e, area="email")


async def send_magic_link_email(email: str, link: str) -> None:
    """寄送 Magic Link 登入信件。設計為在 BackgroundTask 中呼叫，失敗只記錄 log 不拋例外，
    避免 /login/magic-link/request 的回應洩漏「這個 email 是否寄信成功」的資訊。

    2026-10 起改用 Brevo 的 HTTP API；函式簽名與信件內容維持不變，前端不需要跟著改動。
    """
    await _send_brevo(
        email,
        "登入您的帳號",
        (
            f'<p>點擊以下連結登入，連結將於 15 分鐘後失效：</p>'
            f'<p><a href="{link}">{link}</a></p>'
            f"<p>若您沒有要求這封信，請忽略它。</p>"
        ),
        "Magic Link ",
    )


async def send_account_deletion_email(email: str, link: str) -> None:
    """2026-11 網頁刪除帳號申請的確認信（BackgroundTask 中呼叫，失敗只記 log）。"""
    await _send_brevo(
        email,
        "確認刪除您的帳號",
        (
            "<p>我們收到刪除您帳號的申請。請在 30 分鐘內點擊以下連結，"
            "並在頁面上再按一次「確認刪除帳號」才會真的刪除：</p>"
            f'<p><a href="{link}">{link}</a></p>'
            "<p>帳號刪除後無法復原。若您沒有提出這個申請，請忽略這封信，帳號不會有任何變動。</p>"
        ),
        "刪除帳號確認",
    )
