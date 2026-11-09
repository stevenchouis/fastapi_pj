"""
2026-11 錯誤監控（Sentry）的測試端點，只有 X-Admin-Key 能呼叫（詳見 docs/sentry.md）。

- POST /api/v1/monitoring/test-error            → API 直接丟例外（回 500），驗證「API 未處理的例外」會送到 Sentry
- POST /api/v1/monitoring/test-error?source=scheduler → 排一個 1 秒後執行、一定會失敗的一次性排程，
  回 202，驗證「排程執行緒裡的例外」會送到 Sentry
"""
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.api.deps import verify_admin_key
from app.services.scheduler_service import scheduler

router = APIRouter()


class SentryTestError(RuntimeError):
    """刻意觸發的測試錯誤，方便在 Sentry 後台辨識。"""


def _failing_job():
    raise SentryTestError("Sentry 測試錯誤（排程執行緒）")


@router.post("/test-error", dependencies=[Depends(verify_admin_key)])
async def trigger_test_error(source: Literal["api", "scheduler"] = "api"):
    if source == "scheduler":
        scheduler.add_job(
            _failing_job,
            "date",
            run_date=datetime.now(scheduler.timezone) + timedelta(seconds=1),
            id="sentry-test-error",
            replace_existing=True,
        )
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"scheduled": True})
    raise SentryTestError("Sentry 測試錯誤（API）")
