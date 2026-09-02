# 生日禮券 CronJob 冪等性防呆

## 背景

`app/services/scheduler_service.py` 的 `send_birthday_coupons_async_task` 是既有功能（每月 25 日掃描下個月生日的使用者、發放優惠券），原本的程式碼是**無條件**幫每位符合資格的使用者建立一張新優惠券，沒有檢查是否已經發過。

前端在測試核銷功能時提出疑慮：如果這個 job 因為某些原因被觸發兩次（同一 process 重疊執行、或未來多 worker 部署時每個 process 各自的 scheduler 都在同一時間點觸發），同一位使用者會不會收到重複的生日禮券？

## 調查結果

查了實際安裝的 APScheduler 套件原始碼（`schedulers/base.py`）：

- `scheduler_service.py` 呼叫 `add_job()` 時沒有明確傳入 `misfire_grace_time` / `max_instances` / `coalesce`，套用 `BackgroundScheduler` 的預設值：`max_instances=1`、`misfire_grace_time=1`（秒）、`coalesce=True`
- `max_instances=1` 只保護**同一個 process 裡的同一個 scheduler 物件**不會讓同一個 job 重疊執行兩個實例，不保護跨 process
- `misfire_grace_time`／`coalesce` 的預設值代表：單純的 process 重啟本身不會造成重複發券（job 只在真正到達觸發時間點才執行；即使錯過該時間點超過 1 秒也只會跳過，不會補跑）
- **沒有任何跨 process 的保護**：`scheduler = BackgroundScheduler()` 是記憶體內物件，未來如果部署時開多個 worker，每個 process 都會各自建立 scheduler、各自在同一時間點觸發，造成重複發券
- 更根本的問題：即使只有單一 process，程式碼本身也完全沒有防重複的邏輯——只要 job 被觸發兩次，就是無條件重複建立

## 修法

在 `send_birthday_coupons_async_task` 建立優惠券前，先查詢該使用者「這個月的這批優惠券」是否已存在，存在就跳過：

```python
dup_query = select(Coupon.id).where(
    Coupon.user_id == user.id,
    Coupon.title == coupon_title,
    Coupon.expired_at == expired_dt,
)
```

用 `user_id + title + expired_at` 三者比對，而不是只比對 `user_id + title`：因為同一位使用者每年都會過生日，`title`（例如「10月壽星專屬禮券」）每年都會重複，但 `expired_at` 是「本次批次計算出的到期日」（下個月最後一天），可以精準識別「這個月這批」有沒有發過，不會誤擋明年同月份的合法新券。

這個檢查同時解決「單一 process 內重複觸發」和「多 process 各自觸發」兩種情境，比修 APScheduler 本身的分散式鎖來得直接（後者需要引入外部鎖機制如 Redis/資料庫鎖，對這個專案規模是不必要的複雜度）。

## 影響範圍

- `app/services/scheduler_service.py`：`send_birthday_coupons_async_task` 新增防重複查詢，log 訊息改為顯示「實際發放人數」與「跳過的重複觸發人數」
- 不影響 API 或資料庫 schema，不需要 migration

## 後續更新：補上真實推播（2026-09-01）

實作管理者手動發券 API（見 `docs/admin-coupon-issue.md`）時發現生日禮券原本只手動 `db.add(NotificationLog(...))`，不會觸發真實 Expo 推播，跟管理者發券的行為不一致。使用者確認後補上：

- 優惠券建立、commit 完成後，才對每個「本次實際發券成功」的使用者呼叫 `send_user_push_notifications`（跟管理者發券 API 用同一個函式）
- 這個函式會自動建立 `NotificationLog` 並觸發真實推播，所以原本手動 `db.add(NotificationLog(...))` 的程式碼直接移除，避免重複寫入
- 沿用 `app/services/push_service.py` 既有的「先撈資料 → 關閉 DB session → 再發推播」模式：優惠券的資料庫交易先 commit 完、`async with AsyncSessionLocal()` 區塊結束後才開始逐一發推播，推播失敗不會影響已經成功寫入的優惠券，也不會擋住其他人的推播（單獨 try/except 每一位使用者）
