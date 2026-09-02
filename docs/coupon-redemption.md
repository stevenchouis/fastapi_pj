# 優惠券核銷

## 功能概述

使用者在優惠券詳情頁按「使用」，取得一組限時核銷碼（6 位數字，10 分鐘效期）。因為目前還沒有另一支店員核銷 App，先在同一支消費者 App 加「人工核銷」輸入框，讓使用者輸入核銷碼完成核銷——這是**過渡方案**，模擬未來店員掃 QR Code 後呼叫核銷 API 的動作。

> ⚠️ 這是刻意先求有的簡化版：目前 `/coupons/redeem` 是用消費者自己的 JWT 呼叫，等於消費者可以自己核銷自己的優惠券，沒有真正的店員身分驗證。見下方「已知限制」。

## 技術流程

```
App                                後端 (FastAPI)
 |-- POST /coupons/{id}/redeem-code ------------->|
 |                                    |-- 驗證優惠券屬於自己、未使用、未過期
 |                                    |-- 產生 6 位數字碼，存 hash + 10 分鐘效期
 |<--------- { code, expires_at } ----------------|
 |   (App 顯示 QR Code / 碼供人工輸入)
 |
 |-- POST /coupons/redeem { code } --------------->|
 |                                    |-- 原子性 UPDATE：核銷碼有效且未使用 → 標記已使用
 |<--------- { id, title, discount_amount } -------|
```

## 後端實作

### 異動檔案

| 檔案 | 異動內容 |
|---|---|
| `app/models.py` | `Coupon` 新增 `redeem_code_hash`（indexed, nullable）、`redeem_code_expires_at` 欄位 |
| `app/schemas/coupon.py` | 新增 `CouponRedeemCodeOut`、`CouponRedeemRequest`、`CouponRedeemResult` |
| `app/api/v1/endpoints/coupons.py` | 新增 `POST /coupons/{id}/redeem-code`、`POST /coupons/redeem` |
| `testAlembic/versions/5a9ad9941834_*.py` | 新增核銷碼欄位的 migration |

不需要新增任何依賴套件或外部服務，這個功能不用申請任何 API Key。

### API 規格

**POST `/api/v1/coupons/{coupon_id}/redeem-code`**（需登入，且該優惠券必須屬於自己）

Response:
```json
{ "code": "123456", "expires_at": "2026-08-31T15:40:00Z" }
```

錯誤情況：`404` 優惠券不存在（或不屬於自己）、`400` 已使用/已過期。

**POST `/api/v1/coupons/redeem`**（需登入，但不檢查優惠券擁有者）

Request:
```json
{ "code": "123456" }
```

Response:
```json
{ "id": 1, "title": "5月壽星禮", "discount_amount": 100.0 }
```

錯誤情況：`400` 核銷碼無效、已使用、或已過期。

### 業務邏輯與設計取捨

1. **不另建資料表**：核銷碼相關欄位直接加在 `coupons` 表上（`redeem_code_hash`、`redeem_code_expires_at`），因為一張優惠券同一時間只會有一組有效核銷碼，重新產生就是覆蓋舊碼（舊碼自然失效，不需要額外管理）
2. **只存 hash**：跟 Magic Link 的做法一致，資料庫只存 SHA-256 hash，明文碼只在 `/redeem-code` 的回應中出現一次
3. **6 位數字碼的唯一性檢查**：只在「目前仍有效（未過期、未使用）」的核銷碼範圍內檢查是否重複（最多重試 5 次），已過期的舊碼即使雜湊值相同也不影響——因為 6 位數字只有 100 萬種組合，如果對「全部歷史核銷碼」做唯一性檢查，遲早會跟舊碼撞號
4. **原子性核銷**：`/redeem` 用一次 `UPDATE ... WHERE redeem_code_hash=... AND is_used=false AND redeem_code_expires_at > now()` 完成核銷，避免同一組碼被同時打兩次而重複核銷
5. **`/redeem` 不檢查擁有者**：這是刻意的，因為核銷碼本身就是授權憑證（誰知道碼就能核銷），之後改成店員角色呼叫時這裡的邏輯不需要改，只需要在權限層加上「呼叫者必須是店員」的檢查

## 已知限制 / 正式上線前需要處理

1. **權限模型未完成**：`/coupons/redeem` 目前任何登入使用者都能呼叫，沒有店員/商家身分驗證，消費者可以自己核銷自己的優惠券。正式上線前需要設計店員端角色與對應的驗證機制。
2. **核銷碼沒有嘗試次數限制**：6 位數字碼只有 100 萬種組合，`/coupons/redeem` 目前沒有做限流或鎖定機制，理論上存在被寫程式在 10 分鐘效期內暴力窮舉猜中他人核銷碼的風險。這個專案目前規模小、攻擊誘因低，暫不處理，但正式上線前應該跟第 1 點的權限模型一起重新設計（例如：加上呼叫者身分限制後，這個風險會大幅降低；或加上失敗次數限制）。
