import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


async def send_magic_link_email(email: str, link: str) -> None:
    """寄送 Magic Link 登入信件。設計為在 BackgroundTask 中呼叫，失敗只記錄 log 不拋例外，
    避免 /login/magic-link/request 的回應洩漏「這個 email 是否寄信成功」的資訊。

    2026-10 起改用 Brevo 的 HTTP API（走 HTTPS，不會碰到 Render 免費方案可能擋 SMTP
    連線埠的問題）；函式簽名與信件內容維持不變，前端不需要跟著改動。
    """
    if not settings.BREVO_API_KEY or not settings.MAIL_FROM_EMAIL:
        logger.error("BREVO_API_KEY 或 MAIL_FROM_EMAIL 尚未設定，無法寄送 Magic Link 信件")
        return

    payload = {
        "sender": {"name": settings.MAIL_FROM_NAME, "email": settings.MAIL_FROM_EMAIL},
        "to": [{"email": email}],
        "subject": "登入您的帳號",
        "htmlContent": (
            f'<p>點擊以下連結登入，連結將於 15 分鐘後失效：</p>'
            f'<p><a href="{link}">{link}</a></p>'
            f"<p>若您沒有要求這封信，請忽略它。</p>"
        ),
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
            logger.info(f"Magic Link 信件已寄出: {email}")
        else:
            logger.error(
                f"🔥 Magic Link 信件寄送失敗 (status={response.status_code}): {response.text}"
            )
    except Exception as e:
        logger.error(f"🔥 Magic Link 信件寄送失敗: {e}")
