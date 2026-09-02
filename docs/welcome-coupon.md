# 新會員歡迎禮券

## 功能概述

使用者註冊完成時，`POST /api/v1/users/register` 在同一個資料庫交易裡順便發一張新會員歡迎禮券，不用另外呼叫發券 API 或等 CronJob。

- 標題：「新會員歡迎禮券」
- 金額：100
- 效期：30 天

## 技術流程

```
App                          後端 (FastAPI)
 |-- POST /users/register { email, password, birthday? } -->|
 |                              |-- 檢查 email 是否已註冊
 |                              |-- 建立 User，flush 取得 user.id
 |                              |-- 建立 Coupon（同一交易），flush 取得 coupon.id
 |                              |-- COMMIT（User + Coupon 一起寫入）
 |                              |-- BackgroundTask 觸發推播（此時通常還沒有 Push Token，會靜默跳過）
 |<--------- User 物件 --------|
```

## 後端實作

### 異動檔案

| 檔案 | 異動內容 |
|---|---|
| `app/services/coupon_service.py`（新檔案）| 新增 `build_coupon()` helper：建立一張「N 天後到期」的優惠券物件 |
| `app/api/v1/endpoints/users.py` | `/register` 同一交易內建立歡迎禮券、BackgroundTask 觸發推播 |
| `app/api/v1/endpoints/coupons.py` | `admin_issue_coupon` 改用共用的 `build_coupon()`，避免邏輯分岔 |

不需要資料庫 migration。

### 為什麼不需要冪等性檢查

跟生日禮券／管理者發券不同，註冊天生只會發生一次——同一個 email 不能重複註冊（`/register` 一開始就檢查 email 是否已存在），沒有「同一個人被重複觸發」的問題，所以不需要像生日禮券那樣加防重複查詢。

### 為什麼同一交易，而不是 commit 之後再補發

如果 User 建立成功但 Coupon 建立失敗（或反過來），要嘛整批 rollback、要嘛就要另外處理「使用者存在但沒禮券」的補償邏輯。放在同一個交易裡，兩者要嘛一起成功、要嘛一起失敗，不會有中間狀態，最簡單。

### 避開 SQLAlchemy 的「commit 後屬性過期」陷阱

`await db.commit()` 會讓整個 session 裡所有物件的屬性變成過期狀態。如果 commit 之後才去存取 `db_user.id` 或 `welcome_coupon.id`（例如要放進推播的 `data` 裡），在 async 環境下會觸發例外（這是實作管理者發券 API 時踩到、後來修掉的同一個坑，見 `docs/admin-coupon-issue.md`）。這裡的作法是：`db.add(...)` 之後先 `await db.flush()` 讓資料庫指派好 id，把 `user_id`／`coupon_id` 存到一般 Python 變數裡，commit 之後只用這兩個變數，不再去碰 ORM 物件的屬性（`db_user` 例外：commit 後有明確 `await db.refresh(db_user)`，所以它是安全的，這是 `response_model=schemas.User` 需要用到完整欄位的緣故）。

### 為什麼註冊當下通常收不到真推播

前端 `register.tsx` 註冊完不會自動登入，使用者要自己回登入畫面登入後（`completeLogin()`）才會同步 Push Token 到後端。所以 `/register` 這裡呼叫 `send_user_push_notifications` 時，資料庫裡通常還查不到這個新使用者的 Push Token，函式會靜默跳過（不會報錯，只會留下 `NotificationLog`）。這是預期行為：使用者要等登入、Token 同步完，下次有新推播才會真的跳出通知；在那之前只能靠打開 App 自己看到這張禮券。

### 共用 `build_coupon()`，但生日禮券不套用

`build_coupon()` 給「管理者發券」跟「新會員歡迎禮券」共用，因為兩者都是「N 天後到期」的模式。生日禮券（`scheduler_service.py`）的到期日邏輯是「下個月最後一天」，跟「N 天後到期」計算方式本質不同，不是意外重複的程式碼，所以那處維持原樣，不套用這個 helper。

## 測試結果

已用 curl 對一個一次性測試 email 呼叫 `/register`，確認：
- 回傳 `200`，User 物件正常
- 同一筆資料裡確實多了一張「新會員歡迎禮券」（$100，30 天後到期）
- 沒有觸發例外（沒有踩到 commit 後屬性過期的坑）

測試用的使用者跟優惠券已經清除，不留在資料庫裡。
