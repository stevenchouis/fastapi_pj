# 網購訂單金流（綠界 ECPay AioCheckOut/V5）

## 功能概述

`POST /orders` 建立的訂單一直卡在 `status="pending"`（見 `docs/products-and-orders.md` 的已知限制），這次補上實際跟綠界 ECPay 串接的兩支端點：組付款表單（`POST /orders/{id}/checkout`）、接收付款結果通知（`POST /orders/ecpay/callback`）。跟前端 mynotification session 在跨 session 對話中確認過分工與 API 契約：後端負責組表單欄位／算簽章／處理 Server-to-Server 的 `ReturnURL`；前端負責在 App 內 WebView 載入付款頁、用 `onShouldStartLoadWithRequest` 攔截 `ClientBackURL` 的 custom scheme deep link 導回 App。

目前用的是 ECPay 官方公開的固定測試特店（MerchantID `2000132`），正式上線前需要換成正式申請的特店 `MerchantID`／`HashKey`／`HashIV`。

## 技術流程

```
App(WebView)              後端 (FastAPI)                    ECPay
 |-- POST /orders -------->|                                  |
 |<-- 201 {id, status:pending, merchant_trade_no} --|          |
 |                                                              |
 |-- POST /orders/{id}/checkout -->|                            |
 |                          |-- 組 AioCheckOut 欄位 + CheckMacValue
 |<-- {action_url, fields} --|                                  |
 |-- (WebView 組表單 POST) --------------------------------->|
 |                                                    使用者輸入卡號付款
 |                                                              |
 |                          |<-- POST ReturnURL (Server-to-Server, 背景) --|
 |                          |-- 驗 CheckMacValue -->|
 |                          |-- UPDATE Order status=paid, earn_points -->|
 |                          |-- "1|OK" -------------------------------->|
 |                                                              |
 |<-- ECPay 轉址瀏覽器到 ClientBackURL (mynotification://order/{id}/result) --|
 |-- WebView 攔截 custom scheme，導回 App 顯示結果 --|
```

`ReturnURL`（Server-to-Server 背景通知）跟 `ClientBackURL`（使用者瀏覽器/WebView 前景轉址）是兩條獨立、時序不保證先後的路徑——App 收到 `ClientBackURL` 深層連結時不代表 `ReturnURL` 一定已經處理完成，前端該畫面應該重新呼叫 `GET /orders/me` 查真正的 `status`，不能只憑轉址本身當作付款成功的依據。

## 後端實作

### 異動檔案

| 檔案 | 異動內容 |
|---|---|
| `app/core/config.py` | 新增 `ECPAY_MERCHANT_ID`／`ECPAY_HASH_KEY`／`ECPAY_HASH_IV`（不給預設值強迫從 `.env` 讀）、`ECPAY_ACTION_URL`（預設測試環境網址） |
| `app/services/ecpay_service.py` | 新檔案，`generate_check_mac_value`／`verify_check_mac_value`（ECPay 簽章演算法）、`build_checkout_params`（組 AioCheckOut 表單欄位） |
| `app/schemas/order.py` | 新增 `OrderCheckoutOut`（`action_url` + `fields`） |
| `app/api/v1/endpoints/orders.py` | 新增 `POST /{order_id}/checkout`、`POST /ecpay/callback` |

沒有新增/修改任何 DB 欄位或 migration——`Order.status`／`payment_reference`／`paid_at` 這幾個欄位在 Phase 1（`docs/products-and-orders.md`）就已經建好，這次只是把原本沒人寫入的欄位接上實際邏輯。

### API 規格

**POST `/api/v1/orders/{order_id}/checkout`**（需登入，只能對自己名下的訂單呼叫）

```json
// response 200
{
  "action_url": "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5",
  "fields": {
    "MerchantID": "2000132",
    "MerchantTradeNo": "O2609141141110729",
    "MerchantTradeDate": "2026/09/14 19:41:13",
    "PaymentType": "aio",
    "TotalAmount": "7500",
    "TradeDesc": "訂單付款",
    "ItemName": "Sportbike Motorcycle x1",
    "ReturnURL": "https://fastapi-pj-2.onrender.com/api/v1/orders/ecpay/callback",
    "ChoosePayment": "Credit",
    "ClientBackURL": "mynotification://order/4/result",
    "EncryptType": "1",
    "CheckMacValue": "933F55A9B97880A727650FE41DDFCB8"
  }
}
```

前端把 `fields` 組成 HTML form（或直接组 `<form>` 字串餵給 WebView 的 `source.html`）POST 到 `action_url`。

錯誤情況：
- 訂單不存在或不是自己的 → 404
- 訂單 `status` 不是 `pending`（已經付款成功、或已失敗）→ 409，同一張訂單可以重複呼叫這支直到付款成功為止，不會重複扣庫存（扣庫存動作只發生在 `POST /orders`）

**POST `/api/v1/orders/ecpay/callback`**（公開，無 JWT——ECPay 直接呼叫，沒有使用者 session）

接收 `application/x-www-form-urlencoded`，驗證 `CheckMacValue` 通過、且 `RtnCode=="1"` 才會：
1. `Order.status = "paid"`、`payment_reference = TradeNo`、`paid_at = now()`
2. 呼叫 `loyalty_service.earn_points`（依 `total_amount` 換算點數，`amount<=0` 不會產生任何交易紀錄）

固定回應純文字（ECPay 規定格式，不可更改）：
- 驗簽失敗 → `0|CheckMacValueError`
- 找不到對應 `MerchantTradeNo` → `0|OrderNotFound`
- 已經處理過（`status` 已是 `paid`）→ 直接 `1|OK`，不重複處理
- 處理成功，或 `RtnCode != "1"`（標記 `status="failed"`）→ `1|OK`

## 關鍵設計決策

### 為什麼 checkout 跟建立訂單分成兩支端點

`POST /orders` 已經在做「建立 pending 訂單＋原子扣庫存」，這段邏輯不想因為串金流而變動。如果使用者叫出付款頁後中途關掉 WebView、要重新付款，只要重打 `POST /orders/{id}/checkout` 重新拿一次表單欄位（`MerchantTradeDate` 會更新、`CheckMacValue` 重新算），不需要也不應該重新建立訂單、重複扣庫存。

### `ClientBackURL` 為什麼可以直接給 App 的 custom scheme，不用像 LINE 登入那樣做 https 落地頁

`docs/line-login.md` 的落地頁是因為 LINE Developers Console 設定的 Callback URL 只接受 https（平台側固定限制，跟請求內容無關）。ECPay 的 `ClientBackURL` 是**每次呼叫 AioCheckOut 當下由我方當參數傳入**的，不是在特店後台預先設定死的，所以可以直接傳 `mynotification://order/{order_id}/result`；前端也確認是用 App 內 WebView 載入付款頁（不是跳系統瀏覽器），`onShouldStartLoadWithRequest` 可以在真正發出網路請求前就攔截到這個 custom scheme 導頁。

### CheckMacValue 演算法為什麼要整串轉小寫再選擇性還原

這是 ECPay 官方規定的固定流程（所有語言的官方/社群 SDK 都是同一套），源自參考實作用 .NET 的 `HttpUtility.UrlEncode`（產生小寫 hex、且 `-_.!*()` 這幾個符號不跳脫、空白轉 `+`）。Python 的 `urllib.parse.quote_plus` 預設行為不完全一樣（`!*()` 會被跳脫成 `%21%2A%28%29`），所以要先整串 URL encode、轉小寫，再手動把這幾個字元對應的跳脫序列還原成原始符號，兩邊字串才會一致，雜湊出來的 `CheckMacValue` 才會跟 ECPay 那邊算出來的相符。

**2026-09-14 實機測試踩過的坑：`EncryptType=1` 對應的是 SHA256，不是 MD5。** 第一版實作用了 `hashlib.md5`，`EncryptType` 欄位值仍然固定填 `"1"`（這是 ECPay 現行 API 唯一接受的值），但雜湊演算法本身用錯——這是每一筆請求都 100% 重現的錯誤（不是偶發），前端用官方測試信用卡實測第一筆訂單，ECPay 直接回「CheckMacValue Error（10200073）」，連付款方式選擇畫面都出不來。改成 `hashlib.sha256` 後修正。這段程式碼一開始只做了「自己產生、自己驗證能通過」的自洽性測試，這種測法完全不會抓到「兩邊用不同雜湊演算法但都自洽」這類錯誤——**必須拿真實 ECPay 環境跑過一次才算數**，這也是為什麼上一版文件特別註記「還沒有拿官方範例驗證過」。

### `ReturnURL` 為什麼一定要 idempotent

ECPay 對同一筆交易的 Server-to-Server 通知可能重送（例如我方回應逾時、網路問題）。如果沒有先檢查 `order.status == "paid"` 就直接處理，重送會導致 `earn_points` 被呼叫兩次、使用者拿到雙倍點數。目前的作法是收到通知時先查訂單目前狀態，已經是 `paid` 就直接回 `1|OK` 不做任何寫入。

### `TotalAmount` 為什麼用 `round()` 四捨五入

ECPay 標準信用卡付款不支援小數金額，只能傳整數元。目前商品價格是 `Numeric(10,2)`（例如 DummyJSON 匯入資料常見 `19.99`），下單當下算出的 `total_amount` 可能帶小數，送給 ECPay 前用 `round()` 轉整數元。這代表使用者實際刷卡金額跟 `Order.total_amount`（資料庫存的權威金額）可能有 1 元以內的無條件捨去/進位差異——目前先用這個簡單做法上線，如果之後要嚴謹處理（例如要求商品價格本身就是整數元、或改成把差額也記錄下來），需要另外跟業務確認規則。

## 已知限制／待辦

- **簽章演算法已修正一次真實 bug（MD5→SHA256），但尚未跑完一次成功付款**：2026-09-14 前端第一次實機測試就撞到 `EncryptType=1` 誤用 MD5 雜湊（應為 SHA256）的問題，已修正，但**還沒有實際成功跑完一次付款流程**（改完後還在等前端重新實測）。下次前端回報結果前，不應該假設這段程式碼已經沒問題。
- **`TotalAmount` 的小數捨入**：見上方「關鍵設計決策」，目前用簡單 `round()`，還沒有跟業務確認這個誤差是否可接受。
- **`ClientBackURL` fallback 落地頁尚未實作**：前端確認主要路徑是 WebView 直接攔截 custom scheme，目前沒有另外做 https 中繼落地頁當保底；如果之後真的遇到某些機型/情境攔截不到，需要再補。
- **付款失敗（`RtnCode != "1"`）目前只是把 `Order.status` 標成 `"failed"`，沒有把已扣的庫存加回去**——這是延續 `docs/products-and-orders.md` 原本就記錄的「訂單逾時未取消、沒有自動取消機制歸還庫存」的已知限制，這次沒有一併處理。
- **測試環境需要後端本身可被外部連到**：`ReturnURL` 是 ECPay 伺服器主動呼叫，本機 uvicorn＋區網 IP 連不到；測試付款要對正式部署（`https://fastapi-pj-2.onrender.com`，已確認有 Render 保活機制、不會休眠）跑，或另外架設 ngrok 之類的通道。

## 驗證方式

本機啟動 uvicorn，用 `create_access_token` 直接簽一組已知使用者 id 的 JWT（略過登入流程），跑過一次完整端到端流程，皆對同一個 Supabase DB（沒有獨立測試資料庫，比照專案既有慣例）：

1. `POST /orders` 建立一筆訂單（商品單價 NT$7499.99）→ 201，`status="pending"`
2. `POST /orders/{id}/checkout` → 200，`TotalAmount` 正確四捨五入成 `"7500"`，`CheckMacValue` 有值
3. 用相同測試 `HashKey`/`HashIV` 自己組一筆合法的 `RtnCode=1` callback 通知打 `POST /orders/ecpay/callback` → `1|OK`；重新查 `GET /orders/me` 確認該筆訂單 `status` 變成 `"paid"`、`paid_at` 有值、`points_earned` 正確顯示 74（`floor(7499.99/100)`）
4. 重送同一筆 callback（模擬 ECPay 重送）→ 仍回 `1|OK`，`GET /loyalty/transactions` 確認只有一筆對應 `related_order_id` 的 `earn` 紀錄，沒有重複發點
5. 把 `CheckMacValue` 竄改後送 callback → 回 `0|CheckMacValueError`，訂單狀態不受影響

**尚未做的驗證**：真的對 ECPay 測試站送出付款表單、用官方測試信用卡完成一筆付款、確認 ECPay 真的能打通我方 `ReturnURL`（上面第 3-5 步都是自己組合法通知模擬的，不是 ECPay 真實送來的）。
