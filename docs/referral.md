# 推薦好友（Referral）

2026-11-08 由 mynotification 提出（規格：mynotification repo 的 `plan-referral.md`，使用者已確認）。

## 規則

- 每位會員一組固定的 6 碼推薦碼：大寫英數、去掉 0/O/1/I（`CODE_ALPHABET`，32 個字元），輸入不分大小寫、前後空白會去掉。**第一次呼叫 `GET /referrals/me` 時才產生**，不替既有帳號批次回填。
- 新會員填碼當下拿 **$50「好友推薦禮券」**（疊在既有 $100 歡迎禮券之上）；推薦人等新會員**第一筆消費完成**才拿 **$100「推薦好友禮券」**。券都是 30 天、`restaurant_id=null`。
- 第一筆消費完成＝三個賺點觸發點（網購 paid、堂食 completed、會員快速結帳），不設最低金額；之後取消也不收回推薦人的券。
- 推薦人每年（台灣時間自然年）最多拿 **10 次**；超過仍可推薦，新會員照拿 $50，推薦人不拿（之後也不補發）。
- 每位新會員只能被推薦一次、不能改、不能填自己的碼。
- 補填（給 Google／LINE／Magic Link 自動建立、註冊時沒機會填碼的帳號）：**註冊 7 天內**（註冊日台灣時間 + 7 天的 23:59:59）、**還沒消費過**、還沒被推薦過。
- 刪除帳號：被刪那方（推薦人或被推薦人）的推薦紀錄一併刪除，推薦碼清空（之後輸入這組碼會回 `invalid_referral_code`）。
- 不自動加好友。
- 常數集中在 `app/services/referral_service.py` 開頭。

## API

**`GET /api/v1/referrals/me`**（需登入）

```json
{
  "code": "K7Q2MX",
  "successful_count": 3,
  "pending_count": 1,
  "rewarded_this_year": 3,
  "yearly_limit": 10,
  "referred_by": { "display_name": "阿明" },
  "can_apply_code": false,
  "apply_deadline": null
}
```

- `successful_count`：已完成首購的推薦人數（含超過年度上限、推薦人沒拿到券的）；`pending_count`：已填碼、還沒首購。
- `rewarded_this_year`：今年推薦人實際拿到券的次數。
- `referred_by.display_name`：暱稱優先，沒有就遮罩 email（`st***@hotmail.com`），純 LINE 帳號為 `用戶{id}`（`referral_display_name`，跟好友 API 的 `display_name` 格式不同、刻意不共用）。
- `apply_deadline`：只有 `can_apply_code=true` 時才有值，例如 `2026-11-15T23:59:59+08:00`。

**`POST /api/v1/referrals/apply`**（需登入），body `{"code": "K7Q2MX"}`

| 情況 | 回應 |
|---|---|
| 成功 | `201` `{"coupon": <$50 券，同 /coupons/me>, "referred_by": {"display_name": ...}}` |
| 碼不存在（或推薦人已刪帳號） | `400` `invalid_referral_code` |
| 填自己的碼 | `400` `self_referral` |
| 已被推薦過 | `409` `already_referred` |
| 超過 7 天、已消費過、或帳號沒有註冊時間 | `409` `apply_window_closed` |

錯誤 `detail` 都是 `{"error_code", "message"}` 物件。

**`POST /api/v1/users/register`** body 新增可選 `referral_code`：有效 → 同一個 transaction 記錄推薦關係＋發 $50 券；無效 → `400 invalid_referral_code`，整個註冊 rollback；空字串視同沒帶。

**推薦人獎勵推播**：`title`「推薦好友成功！」、`body`「您推薦的好友完成第一筆消費，送您一張 $100 推薦好友禮券」、`data` `{"type": "referral_rewarded", "screen": "Coupons", "coupon_id": <id>}`，`app_id="mynotification"`。新會員的 $50 券不另外推播。

## 資料

- **`User.created_at`**（新欄位）：之前 `User` 沒有註冊時間，無法判斷「註冊 7 天內」。migration `1f52a8bef9b6` **刻意分兩步加**：先加沒有預設值的欄位（既有 16 個帳號維持 NULL），再設 `server_default=now()`。直接帶預設值加欄位會讓 PostgreSQL 把既有帳號都填成 migration 當下時間，等於讓所有沒消費過的舊帳號在上線後 7 天內都能補填。**`created_at` 是 NULL 的帳號一律視為補填期限已過。**
  - 坑：測試時如果在 ORM 物件上設 `created_at=None`，SQLAlchemy 會在 INSERT 時省略這個欄位、讓資料庫預設值 `now()` 生效，不會真的寫入 NULL；要 NULL 得另外下 UPDATE。
- **`User.referral_code`**：`String(6)`、unique、nullable。產生時先查撞號，查完到寫入之間被搶走的極小機率由 unique 限制＋savepoint 重試處理。
- **`Referral`**（`referrals`）：`referrer_id`、`referee_id`（**unique**，一人只能被推薦一次）、`referee_coupon_id`、`first_purchase_at`、`referrer_rewarded_at`、`referrer_coupon_id`、`created_at`。兩個券外鍵都是 `ON DELETE SET NULL`（刪帳號會先刪未使用的券）。年度上限用 `referrer_rewarded_at` 落在今年的筆數計算，不另開表。

## 首購獎勵怎麼觸發

`loyalty_service.process_tier_earn`（三個賺點觸發點共用）最後呼叫 `referral_service.process_first_purchase(db, user_id)`：

1. 找「這個人被推薦、而且還沒記過首購」的 `Referral`（`FOR UPDATE`）；沒有就回 `None`。補填規定「還沒消費過」，所以只要有這列且沒記首購，這筆就是首購。
2. 記 `first_purchase_at`。
3. 鎖推薦人的 `User` 列，序列化「不同新會員同時首購、都算到同一個推薦人」的情況，數今年已領次數；未滿 10 → 發 $100 券、記 `referrer_rewarded_at`。
4. 回傳 `{"referrer_id", "coupon_id"}` 放進 `process_tier_earn` 結果的 `referral` 欄位。

推播統一改由 **`loyalty_service.queue_reward_pushes(background_tasks, user_id, result)`** 在 commit 後排程（升等推播＋推薦獎勵推播），三個觸發點原本各自複製的升等推播程式碼都換成這一行。

**已知的極端情況**：A 推薦 B、B 又推薦 A（互相推薦沒有被禁止），而且兩人同時完成消費時，兩個交易會以相反順序鎖對方的 `User` 列，PostgreSQL 會偵測到死結、讓其中一筆失敗（ECPay callback 會重送；店員端會看到 500 需重按）。發生機率極低，沒有特別處理。

## 驗證（2026-11-08）

用 `httpx.ASGITransport` 打真實端點、連正式 DB，整個測試包在外層交易內（`join_transaction_mode="create_savepoint"`），最後 rollback，事後確認沒有殘留。推播函式換成假的、不寫 NotificationLog。

- `GET /me` 產生 6 碼（字元都在字母表內）、再查一次同一組碼、新帳號 `can_apply_code=true`、`apply_deadline` 為 `…T23:59:59+08:00`
- 註冊帶**小寫**推薦碼 → 成功，新會員有「新會員歡迎禮券 $100」＋「好友推薦禮券 $50」；註冊帶無效碼 → 400 `invalid_referral_code` 且帳號沒建立
- 被推薦者 `referred_by.display_name` 是推薦人暱稱、不能再補填（409 `already_referred`）
- 補填：自己的碼 400 `self_referral`、無效碼 400 `invalid_referral_code`、前後有空白的正確碼 201（$50、連鎖層級）
- 註冊 9 天前、`created_at` NULL、已消費過 → `can_apply_code=false` 且補填回 409 `apply_window_closed`
- 被推薦者首購 → 推薦人拿 $100「推薦好友禮券」；第二筆不再發；推播 payload 正確
- 推薦人今年已領 10 次 → 第 11 位首購不發券、但記首購；`successful_count`／`pending_count`／`rewarded_this_year` 正確
- 被推薦者刪帳號 → 推薦紀錄刪除；推薦人刪帳號 → 推薦紀錄刪除、推薦碼清空、這組碼無法再用
- 回歸：會員等級、每日簽到、點數兌換三組既有測試重跑全部通過

**沒有驗證到的**：真正的並發（兩人同時首購同一個推薦人、兩支手機同時補填，都依資料庫鎖／unique 限制推論）、推播實際送達、前端實機。
