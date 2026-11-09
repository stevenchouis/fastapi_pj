"""
2026-11 錯誤監控（Sentry），詳見 docs/sentry.md。

- SENTRY_DSN 沒設就完全不啟用（本機開發、驗證腳本不會回報）
- environment：SENTRY_ENVIRONMENT 有設就用它；否則在 Render 上（Render 會自動帶 RENDER 環境變數）
  是 "production"，其他地方是 "local"
- 不送個資：不送 request body、cookie、query string（Magic Link／刪除帳號連結的 token 在這裡）、
  除了白名單以外的 header（Authorization、X-Admin-Key 都不送）、stack frame 的區域變數；
  所有文字再過一次過濾，遮掉 Email、Expo push token、SQL 參數、token=… 這類字串。
  使用者只帶 id（deps.get_current_user 設定）
- 回報範圍：API 未處理的例外與 5xx（FastAPI 整合預設）、排程裡的例外（APScheduler 監聽器＋
  各排程原本 try/except 吞掉的地方明確 capture）、推播與寄信這類背景工作的非預期失敗。
  logging 不轉成 Sentry 事件／麵包屑（目前的 log 會帶 Email、token 片段，而且
  DeviceNotRegistered 這類預期中的錯誤也是用 logger.error 記，會洗版）
"""
import os
import re

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

from app.core.config import settings

_HEADER_ALLOWLIST = {"user-agent", "content-type", "content-length", "host", "accept", "x-forwarded-for"}
_REDACTIONS = [
    (re.compile(r"\[parameters: .*?\]", re.S), "[parameters: <redacted>]"),
    (re.compile(r"ExponentPushToken\[[^\]]*\]"), "ExponentPushToken[<redacted>]"),
    (re.compile(r"(?i)(token|refresh_token|password|api-key|api_key)(=|\":\s*\"|':\s*')[^&\s\"']+"), r"\1\2<redacted>"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "<email>"),
]


def sentry_environment() -> str:
    if settings.SENTRY_ENVIRONMENT:
        return settings.SENTRY_ENVIRONMENT
    return "production" if os.environ.get("RENDER") else "local"


def _scrub_text(value: str) -> str:
    for pattern, repl in _REDACTIONS:
        value = pattern.sub(repl, value)
    return value


def _scrub(obj):
    if isinstance(obj, str):
        return _scrub_text(obj)
    if isinstance(obj, dict):
        return {k: _scrub(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_scrub(v) for v in obj)
    return obj


def _before_send(event, hint):
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "query_string", "env"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {k: v for k, v in headers.items() if k.lower() in _HEADER_ALLOWLIST}
        if isinstance(request.get("url"), str):
            request["url"] = request["url"].split("?", 1)[0]
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user["id"]} if "id" in user else {}
    return _scrub(event)


def _before_breadcrumb(crumb, hint):
    return _scrub(crumb)


def init_sentry() -> bool:
    """在 app 建立前呼叫。回傳是否有啟用。"""
    if not settings.SENTRY_DSN:
        return False
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=sentry_environment(),
        send_default_pii=False,
        traces_sample_rate=0,
        max_request_body_size="never",
        include_local_variables=False,
        integrations=[LoggingIntegration(level=None, event_level=None)],
        before_send=_before_send,
        before_breadcrumb=_before_breadcrumb,
    )
    return True


def capture_exception(exc: BaseException, **tags) -> None:
    """排程、背景工作原本 try/except 吞掉的例外用這個回報（Sentry 沒啟用時什麼都不做）。"""
    with sentry_sdk.new_scope() as scope:
        for key, value in tags.items():
            scope.set_tag(key, value)
        scope.capture_exception(exc)


def capture_message(message: str, level: str = "error", **tags) -> None:
    """沒有例外物件的失敗（例如 Brevo 回非 2xx）用這個回報。"""
    with sentry_sdk.new_scope() as scope:
        for key, value in tags.items():
            scope.set_tag(key, value)
        scope.capture_message(_scrub_text(message), level=level)
