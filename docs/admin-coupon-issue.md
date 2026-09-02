# 管理者手動發券 API

## 功能概述

給管理者（目前就是使用者本人）用 Postman 手動呼叫的發券端點，用於活動加碼、客訴補償這類情境。不做前端管理介面，前端完全不用改任何程式碼。

## API 規格

**POST `/api/v1/coupons/admin/issue`**

這個端點**不會出現在 `/docs` 的 Swagger 文件裡**（`include_in_schema=False`），必須直接知道路徑才能呼叫。

Headers:
```
X-Admin-Key: <ADMIN_API_KEY>
Content-Type: application/json
```

Request:
```json
{
  "user_email": "user@example.com",
  "title": "客訴補償券",
  "discount_amount": 200,
  "valid_days": 30
}
```

- `discount_amount` 必須 > 0，否則回 `422`
- `valid_days` 選填，預設 `30`，必須 > 0

Response（`200`）：
```json
{
  "id": 6,
  "user_id": 1,
  "title": "客訴補償券",
  "discount_amount": 200.0,
  "is_used": false,
  "used_at": null,
  "expired_at": "2026-10-01T02:14:00.000000+00:00"
}
```

錯誤情況：
- `401`：`X-Admin-Key` 缺少或不正確
- `404`：`user_email` 查無使用者
- `422`：`discount_amount` 或 `valid_days` 不是正數、或 email 格式不對

## 後端實作

### 異動檔案

| 檔案 | 異動內容 |
|---|---|
| `app/core/config.py` | 新增 `ADMIN_API_KEY` 設定 |
| `app/api/deps.py` | 新增 `verify_admin_key` dependency，比對 `X-Admin-Key` header |
| `app/schemas/coupon.py` | 新增 `AdminIssueCouponRequest` |
| `app/api/v1/endpoints/coupons.py` | 新增 `POST /coupons/admin/issue` |

不需要資料庫 migration（沒有新增欄位）。

### 認證方式：為什麼用固定密鑰而不是 RBAC

`users` 表目前沒有 `role` / `is_admin` 的概念，這支端點的濫用風險也低（頂多是被拿去發免費優惠券）。比起另外設計一套管理者角色系統，用一組存在 `.env` 的固定密鑰（`X-Admin-Key` header 比對）更務實。比對用 `secrets.compare_digest` 做 constant-time 比較，避免用一般字串比對可能洩漏比對進度的 timing attack。

## 使用者需要做的事

`ADMIN_API_KEY` 是我自己產生的高熵亂數字串，不需要跟任何外部服務申請，已經直接設進 `.env`。用 Postman 呼叫時，把這個值放進 `X-Admin-Key` header 即可。

> ⚠️ 這組密鑰請不要外流或提交進 git（`.env` 已經在 `.gitignore` 裡，正常操作不會被 commit，但要留意別把它貼到聊天以外的地方，例如公開的截圖或文件）。

## 推播通知：發現一個既有的不一致之處

這支 API 呼叫 `app/services/push_service.py` 的 `send_user_push_notifications`——這個函式會**同時**寫入 `NotificationLog`（App 內收件匣）**並**觸發真實的 Expo 推播（手機會跳系統通知）。

但既有的生日禮券邏輯（`app/services/scheduler_service.py`）只有手動 `db.add(NotificationLog(...))`，**沒有**呼叫 `send_user_push_notifications`，所以生日禮券目前只會出現在 App 的通知收件匣，手機不會跳出系統推播通知。

也就是說：**管理者手動發券會觸發真實手機推播，生日禮券不會**。這個不一致是原本就存在的（不是這次新增造成的），先記錄在這裡；如果之後要讓生日禮券也觸發真推播，只要把 `scheduler_service.py` 手動建立 `NotificationLog` 的地方改成呼叫 `send_user_push_notifications` 即可，是一個獨立的小修改，目前還沒有處理。

## 測試結果

已用 curl 驗證：
- 缺少 `X-Admin-Key` → `422`
- `X-Admin-Key` 錯誤 → `401`
- 正確 Key 但 `user_email` 查無使用者 → `404`
- 正確 Key 但 `discount_amount` 為負數 → `422`
- 確認 `/openapi.json` 裡沒有這條路徑（`include_in_schema=False` 生效）

沒有實際跑過成功建立優惠券的完整路徑（會觸發真實推播通知到使用者手機，故意避免在沒有事先告知的情況下觸發）；程式碼路徑跟先前已驗證過的優惠券建立/推播邏輯共用，風險低。
