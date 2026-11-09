# 電子發票載具（手機條碼）

2026-11-09 由 mynotification 提出（規格：mynotification repo 的 `plan-einvoice-carrier.md`，使用者已確認）。

## 範圍

- 這期只做「會員綁定載具＋App 出示條碼＋訂單記錄載具」，**不真正開發票**（之後串綠界電子發票另開 plan）。
- 載具存在後端會員資料（`User.einvoice_carrier`），跨裝置一致。
- **只檢查格式、不向財政部驗證**載具是否真的存在。

## 格式

財政部手機條碼：「/」加 7 碼，每碼為 `0-9`、`A-Z`、`.`、`+`、`-`。後端先 normalize（去掉所有空白、轉大寫）再比對 `^/[0-9A-Z.+\-]{7}$`。邏輯在 `app/services/einvoice_service.py`（`normalize_carrier`／`is_valid_carrier`）。全形字元不會被轉換，會被當成格式錯誤。

## API

**`GET /api/v1/users/me`**：回應新增 `einvoice_carrier: string | null`。

**`PUT /api/v1/users/me`**：可帶 `einvoice_carrier`。

| 請求 | 結果 |
|---|---|
| 沒帶這個欄位 | 不動（既有改暱稱／頭像的呼叫不受影響；用 Pydantic `model_fields_set` 判斷「沒帶」跟「帶 null」） |
| `null`、空字串、純空白 | 清除（存 null） |
| 合格字串 | normalize 後存入，例如 `" /ab c+.-1 "` → `"/ABC+.-1"` |
| 不合格字串 | `400` `{"error_code": "invalid_einvoice_carrier", "message": ...}`，原值不變 |

錯誤刻意不在 Pydantic validator 裡丟（那會變成 422），而是在 endpoint 檢查後回 400 + `error_code`，跟前端對過的契約。

**訂單快照**：`POST /orders`、`POST /dine-in-orders`、`POST /store-checkouts` 建立時，後端自動把該會員當下的 `einvoice_carrier` 複製到訂單上（前端不傳）；之後會員改載具不影響舊訂單。門市收銀記的是**會員**的載具（從會員碼 `UPDATE ... RETURNING` 一併取回），不是店員的。三種訂單的所有回應（`OrderOut`／`DineInOrderOut`／`StoreCheckoutOut`，含 `/me` 列表、店員端列表）都有 `einvoice_carrier: string | null`；上線前的舊訂單是 null。

**`POST /api/v1/store-checkouts/lookup`**：回應新增 `einvoice_carrier`，是會員**目前**的載具，給店員 App 顯示。

## 刪除帳號

- `User.einvoice_carrier` 清空。
- 三種訂單本身保留（既有規則），**但訂單上的載具快照也一併清空**。理由：手機條碼在財政部對應到真實手機號碼，留著等於能把已匿名化的帳號連回本人；而且這期還沒真正開發票，快照沒有用途。**之後串接開發票時要重新評估**——屆時已開立發票的載具應由發票紀錄保存，但「已付款、發票還沒開」的訂單如果被刪帳號，要決定是先開完再清，還是改成開紙本／捐贈。

## 資料

migration `c46a384d6d33`：`users`／`orders`／`dine_in_orders`／`store_checkouts` 各加 `einvoice_carrier`（`String(8)`、nullable），純加欄位，既有資料都是 null。

## 驗證（2026-11-09）

用 `httpx.ASGITransport` 打真實端點、連正式 DB，外層交易最後 rollback，事後確認沒有殘留（測試帳號 0 筆、四張表的載具欄位全部 null）。推播換成假的。

- `GET /me` 預設 null；PUT 小寫＋空白正確 normalize；只改暱稱不動載具；`/ABC123`、`/ABC12345`、`ABC12345`、`/ABC_123`、全形字都回 400 `invalid_einvoice_carrier` 且原值不變；空白字串與 null 都清除
- 沒綁載具時下單快照是 null；綁定後網購、堂食、門市收銀三種訂單都記到會員的載具；`/store-checkouts/lookup` 回會員目前的載具
- 會員改載具後，三個 `/me` 列表與店員堂食接單列表都還是顯示下單當下的值
- 刪除帳號後會員與三種訂單的載具都清空，訂單本身還在
- 回歸：會員等級、每日簽到、點數兌換、推薦好友四組既有測試重跑全部通過

**沒有驗證到的**：前端實機。
