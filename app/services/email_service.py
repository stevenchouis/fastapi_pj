import asyncio
import logging

import resend

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_magic_link_email(email: str, link: str) -> None:
    """寄送 Magic Link 登入信件。設計為在 BackgroundTask 中呼叫，失敗只記錄 log 不拋例外，
    避免 /login/magic-link/request 的回應洩漏「這個 email 是否寄信成功」的資訊。"""
    if not settings.RESEND_API_KEY:
        logger.error("RESEND_API_KEY 尚未設定，無法寄送 Magic Link 信件")
        return

    resend.api_key = settings.RESEND_API_KEY
    params: resend.Emails.SendParams = {
        "from": "onboarding@resend.dev",
        "to": [email],
        "subject": "登入您的帳號",
        "html": (
            f'<p>點擊以下連結登入，連結將於 15 分鐘後失效：</p>'
            f'<p><a href="{link}">{link}</a></p>'
            f"<p>若您沒有要求這封信，請忽略它。</p>"
        ),
    }

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, resend.Emails.send, params)
        logger.info(f"Magic Link 信件已寄出: {email}")
    except Exception as e:
        logger.error(f"🔥 Magic Link 信件寄送失敗: {e}")
