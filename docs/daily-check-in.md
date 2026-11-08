# 每日簽到（Daily Check-in）

2026-11-08 由 mynotification 提出（規格：mynotification repo 的 `plan-daily-check-in.md`，使用者已定案）。

## 規則

- 每天 1 點，每輪第 7 天 5 點（一輪 11 點），簽滿 7 天重新一輪；中斷一天從第 1 天重算。
- 「一天」以台灣時間 00:00 為界；昨天有簽、今天簽才算連續。
- `cycle_day = ((streak - 1) % 7) + 1`：第 8 天回到 1 點，第 14、21… 天都是 5 點。
- 簽到點數跟消費點數一樣 **1 年後過期**、被折抵／兌換時**依到期日先後扣（FIFO）**、到期前 7 天會被提醒。
- **不套會員等級倍率、不計入 `year_spend`**（`year_spend` 只從訂單／堂食／門市結帳金額算，本來就不受影響）。
- 店員帳號（`role="staff"`）不擋，店員也可以是會員。
- 不推播提醒簽到。
- 獎勵寫死在 `app/api/v1/endpoints/check_in.py` 的 `CHECK_IN_REWARDS = [1,1,1,1,1,1,5]`；前端照 API 回傳的 `rewards` 顯示，不寫死。

## API

**`GET /api/v1/check-in/status`**（需登入）

```json
{ "checked_in_today": false, "streak": 3, "cycle_day": 4, "today_reward": 1, "rewards": [1,1,1,1,1,1,5] }
```

| 狀況 | streak | cycle_day | today_reward |
|---|---|---|---|
| 今天已簽 | 今天那列的 streak | 今天是這輪第幾天 | 今天領到的點數 |
| 今天沒簽、昨天有簽 | 昨天那列的 streak | 今天簽的話是第幾天 | 今天簽可領幾點 |
| 都沒簽 | 0 | 1 | 1 |

昨天剛好簽滿第 7 天時，`streak=7`、`cycle_day=1`（前端顯示新一輪），已跟前端確認。

**`POST /api/v1/check-in`**（需登入，不需 body）

- 成功 `201`：`{"points_awarded": 1, "streak": 4, "cycle_day": 4, "balance": 1235}`
- 今天已簽過：`409` `{"error_code": "already_checked_in", "message": "今天已經簽到過了"}`

## 資料

**`DailyCheckIn`**（`daily_check_ins`，migration `027c10b372e4`）：`user_id`、`check_in_date`（台灣日期）、`streak`（含這天的連續天數）、`points`、`created_at`，`UniqueConstraint(user_id, check_in_date)`。

- streak 在簽到當下存進來（昨天那列的 streak + 1，否則 1），查狀態只需要看今天、昨天兩列，不用掃歷史。
- 刪除帳號（`DELETE /users/me`）會一併刪除簽到紀錄。

**點數明細**：type **`check_in`**（正向），`reason` 是「每日簽到（第 N 天）」或「每日簽到（連續 7 天加碼）」，`remaining_amount`／`expires_at` 跟 `earn` 一樣。

- `loyalty_service.POINT_LOT_TYPES = ("earn", "check_in")`：「會產生一批有效期限點數」的 type。`redeem_points` 的 FIFO、`expire_loyalty_points_task`、`send_points_expiry_reminder_task` 三處查詢都改用這個常數；之後新增同類 type 加在這裡即可。
- `earned_points_by_related`（訂單回應的 `points_earned`）刻意維持只算 `earn`，簽到不屬於任何訂單。
- `LoyaltyTransactionOut.type` 的允許值加了 `check_in`——沒加的話 `/loyalty/transactions` 遇到簽到明細會 500。

## 重複簽到

先查今天有沒有那列，有就直接 409。兩支手機同時按、兩邊都查不到時，第二個 INSERT 會撞唯一限制（`IntegrityError`）→ rollback（點數也不會入帳，同一個 transaction）→ 409。

## 驗證（2026-11-08）

用 `httpx.ASGITransport` 打真實端點、連正式 DB，整個測試包在外層交易內（`join_transaction_mode="create_savepoint"`），最後 rollback，事後確認沒有殘留：

- 新帳號狀態 `streak=0, cycle_day=1, today_reward=1`；簽到 201、餘額 1；再簽 409 `already_checked_in`；狀態變已簽
- 明細 `check_in`、reason「每日簽到（第 1 天）」、`expires_at` 約一年後
- 昨天第 6 天 → 狀態顯示今天第 7 天可領 5 → 簽到得 5 點、reason「連續 7 天加碼」
- 昨天第 7 天 → 狀態 `streak=7, cycle_day=1` → 簽到為第 8 天、1 點；昨天第 13 天 → 第 14 天 5 點
- 前天有簽、昨天沒簽 → `streak=0`，簽到從第 1 天
- 直接重複 INSERT 同一天會撞唯一限制
- 只有簽到點數時折抵扣得到（FIFO 包含 `check_in`）

**沒有驗證到的**：真正同時兩個請求（靠唯一限制，依資料庫行為推論）、到期收回／提醒排程實跑（查詢條件已改，排程本身沒跑）、前端實機。
