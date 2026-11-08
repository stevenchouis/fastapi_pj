# app/schemas/check_in.py
# 2026-11 每日簽到（跟 mynotification plan-daily-check-in.md 對過的契約）
from typing import List

from pydantic import BaseModel


class CheckInStatusOut(BaseModel):
    checked_in_today: bool
    # 目前連續天數：今天已簽就含今天；今天沒簽但昨天有簽＝到昨天為止的天數；都沒有＝0
    streak: int
    # 今天已簽＝今天是這輪第幾天；還沒簽＝今天簽的話會是第幾天（1～7）
    cycle_day: int
    today_reward: int  # 今天簽到可領（或已領）的點數
    rewards: List[int]  # 一輪 7 天各天的點數，前端照這個顯示


class CheckInOut(BaseModel):
    points_awarded: int
    streak: int
    cycle_day: int
    balance: int  # 入帳後的點數餘額
