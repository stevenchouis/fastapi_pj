# 清除失效的推播 token＋登出解除推播

2026-11-09 由 mynotification 提出（規格：mynotification repo 的 `plan-push-token-cleanup.md`，使用者已確認）。這次處理的是 CLAUDE.md「推播通知」一節記下的技術債：原本完全沒讀 Expo 的回執，失效的 token 會一直累積。

## 判斷失效的三條路徑

| 路徑 | 時機 | 動作 |
|---|---|---|
| Ticket 階段 | 發送當下 Expo 回 `DeviceNotRegistered` | 直接刪該 token（`push_service._save_push_results`） |
| Receipt 階段 | 排程每 15 分鐘查一次已發送滿 15 分鐘的 ticket（`push_cleanup_service.check_push_receipts_task`） | receipt 是 `DeviceNotRegistered` 就刪 token |
| 90 天沒出現 | 每天台灣時間 03:00（`delete_stale_push_tokens_task`） | 刪 `last_seen_at` 超過 90 天的 token |

`MessageTooBig`、`InvalidCredentials`、`MessageRateExceeded` 等其他錯誤只記 log，不刪 token。

### Receipt 查詢細節

- 發送成功（ticket `status=ok`）時，把 ticket id 和當時的 token 字串存進新表 `push_tickets`（migration `877197b3840b`）。token 不是外鍵，因為 token 之後可能被刪或換人。
- 查詢時機用排程，不是「下次發送時順便查」。原因：發送可能隔很久才有下一次，會錯過 Expo receipt 只保留 24 小時的期限。Render 沒有常駐 worker，但既有的保活任務每 14 分鐘 ping 一次，讓服務一直醒著，所以排程會照常觸發。服務重啟也沒關係，ticket 存在 DB，下次觸發會接著查。
- 每次最多查 1000 筆（Expo 單次上限），由舊到新。
- 回應裡有的 ticket 處理完就刪掉，不論成功或失敗。回應裡沒有的代表 receipt 還沒好，留到下次再查。
- 超過 24 小時還沒查到的 ticket 直接刪除。
- 整批查詢失敗（Expo 掛掉、網路問題）只記 log，ticket 留著下次再查。
- 網路呼叫時不佔用 DB 連線，跟 `send_user_push_notifications` 同一個原則：先撈資料、關掉連線，再呼叫 Expo。

## `PushToken.last_seen_at`

- 新欄位，NOT NULL，`server_default=now()`，有 index。
- App 每次打開都會打 `POST /users/push-tokens`（既有行為），新建或更新都會把 `last_seen_at` 設成現在。
- migration 把既有 token 填成上線當下，等於給舊 token 90 天緩衝。刻意不用 `created_at` 回填：之前 token 重新註冊只會更新既有那一列、不會改 `created_at`，用它回填會把還在用的 token 誤刪。

## API

- **`POST /api/v1/users/push-tokens`**：行為跟以前一樣，回應格式不變（204）。同一個 token 已存在時更新擁有者、`device_name`、`app_id`、`last_seen_at`，不會重複建立；以 `token` 為唯一鍵，同一支手機換人登入只會推給目前的人。這段本來就是這樣，這次只多記 `last_seen_at`。
- **`POST /api/v1/users/push-tokens/unregister`**（需登入），body `{token}` → 204。只刪屬於目前使用者的那一筆，找不到（已被清掉、已換人登入）也回 204。沒帶 Authorization 回 401，body 缺欄位回 422。

## 前端注意

- 登出順序：先打 `/users/push-tokens/unregister`（需要有效的通行證；過期就先走 `/login/refresh`），再打 `/logout` 作廢續期證，最後清本機資料。
- 被清掉的 token 不會通知前端。App 下次打開照常打 `POST /users/push-tokens` 就會重新建立，所以不需要額外處理。

## 驗證（2026-11-09）

用 `httpx.ASGITransport` 打真實端點、連正式 DB，Expo client 換成假的（沒有真的發推播），外層交易最後 rollback。事後確認測試帳號、測試 token、`push_tickets` 都沒有殘留。

- 註冊會記 `last_seen_at`；另一個帳號註冊同一個 token 後只剩一筆，擁有者和 `app_id` 都改過來，`last_seen_at` 也更新
- unregister：沒登入回 401；別人的 token 刪不掉（204 但資料還在）；自己的被刪掉；不存在的回 204；缺 body 回 422
- Ticket 階段：`DeviceNotRegistered` 的 token 被刪；`MessageTooBig`、`InvalidCredentials` 的 token 保留；只有成功的那筆記了 ticket；別的 App 的 token 沒被發送
- Receipt 階段：
  - 只查滿 15 分鐘的 ticket，5 分鐘的不查
  - 超過 24 小時的 ticket 直接刪
  - receipt `DeviceNotRegistered` 的 token 被刪，`MessageTooBig` 的保留
  - 還沒好的 ticket 留著
  - Expo 掛掉時 ticket 留著
- 90 天：91 天前的被刪，89 天前的保留
- 三支發送函式（使用者／收藏者／角色）都有帶 `db_factory`，都會記 ticket、刪失效 token
- 回歸：電子發票（含刪除帳號）、refresh token 驗證腳本通過

**沒有驗證到的**：真實 Expo 回應（用假的 client）、前端實機。
