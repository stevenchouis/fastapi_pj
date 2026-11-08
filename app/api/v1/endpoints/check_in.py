# app/api/v1/endpoints/check_in.py
"""
2026-11 每日簽到（mynotification plan-daily-check-in.md，使用者 2026-11-08 定案）：
每天 1 點、每輪第 7 天 5 點，簽滿 7 天重新一輪，中斷一天從第 1 天重算；台灣時間
00:00 換日。點數寫成 type="check_in" 的明細，跟消費回饋一樣 1 年後過期、被折抵依
到期日 FIFO 扣（loyalty_service.POINT_LOT_TYPES）。不套會員等級倍率、不計入
year_spend（year_spend 只從訂單金額算，本來就不受影響）。店員帳號不擋。
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import DailyCheckIn, User
from app.schemas.check_in import CheckInOut, CheckInStatusOut
from app.services import loyalty_service

router = APIRouter()

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
# 一輪 7 天各天的點數，寫死在後端（前端照 GET /status 回傳的 rewards 顯示）
CHECK_IN_REWARDS = [1, 1, 1, 1, 1, 1, 5]


def _taipei_today() -> date:
    return datetime.now(TAIPEI_TZ).date()


def _cycle_day(streak: int) -> int:
    """連續第 streak 天是這輪的第幾天（1～7）；第 8 天回到 1，第 14、21… 天都是 7。"""
    return ((streak - 1) % len(CHECK_IN_REWARDS)) + 1


async def _get_row(db: AsyncSession, user_id: int, day: date) -> DailyCheckIn | None:
    result = await db.execute(
        select(DailyCheckIn).where(
            DailyCheckIn.user_id == user_id, DailyCheckIn.check_in_date == day
        )
    )
    return result.scalars().first()


def _already_checked_in() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={"error_code": "already_checked_in", "message": "今天已經簽到過了"},
    )


@router.get("/status", response_model=CheckInStatusOut)
async def get_check_in_status(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """今天簽了沒、目前連續天數、今天可領幾點。"""
    today = _taipei_today()
    today_row = await _get_row(db, current_user.id, today)
    if today_row:
        return CheckInStatusOut(
            checked_in_today=True,
            streak=today_row.streak,
            cycle_day=_cycle_day(today_row.streak),
            today_reward=today_row.points,
            rewards=CHECK_IN_REWARDS,
        )

    yesterday_row = await _get_row(db, current_user.id, today - timedelta(days=1))
    streak = yesterday_row.streak if yesterday_row else 0
    cycle_day = _cycle_day(streak + 1)
    return CheckInStatusOut(
        checked_in_today=False,
        streak=streak,
        cycle_day=cycle_day,
        today_reward=CHECK_IN_REWARDS[cycle_day - 1],
        rewards=CHECK_IN_REWARDS,
    )


@router.post("", response_model=CheckInOut, status_code=201)
async def check_in(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    簽到。寫簽到紀錄＋入帳點數在同一個 transaction。重複簽到靠 (user_id, check_in_date)
    唯一限制擋：先查是為了一般情況直接回 409；兩支手機同時按、兩邊都查不到時，
    第二個 INSERT 會撞唯一限制（IntegrityError）→ rollback（點數也不會入帳）→ 409。
    """
    user_id = current_user.id
    today = _taipei_today()

    try:
        if await _get_row(db, user_id, today):
            raise _already_checked_in()

        yesterday_row = await _get_row(db, user_id, today - timedelta(days=1))
        streak = yesterday_row.streak + 1 if yesterday_row else 1
        cycle_day = _cycle_day(streak)
        points = CHECK_IN_REWARDS[cycle_day - 1]

        db.add(
            DailyCheckIn(
                user_id=user_id, check_in_date=today, streak=streak, points=points
            )
        )
        await db.flush()

        if cycle_day == len(CHECK_IN_REWARDS):
            reason = f"每日簽到（連續 {cycle_day} 天加碼）"
        else:
            reason = f"每日簽到（第 {cycle_day} 天）"
        await loyalty_service.earn_points(
            db, user_id, points, reason=reason, txn_type="check_in"
        )
        await db.commit()
    except HTTPException:
        raise
    except IntegrityError:
        await db.rollback()
        raise _already_checked_in()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 每日簽到失敗: {e}")
        raise HTTPException(status_code=500, detail="簽到失敗")

    balance = (
        await db.execute(select(User.loyalty_balance).where(User.id == user_id))
    ).scalar_one()
    return CheckInOut(
        points_awarded=points, streak=streak, cycle_day=cycle_day, balance=balance
    )
