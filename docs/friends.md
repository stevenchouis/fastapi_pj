# 好友系統

## 需求背景

前端（mynotification App，規劃在其 repo 的 `plan-friends.md`）想做類似 Pokémon GO／Pikmin Bloom 的好友卡片體驗：顧客出示自己的 QR Code（前端內容為 `mynotification://friends/add?user_id=<id>`），另一位顧客用相機掃描後送出好友邀請，**對方確認後才成立好友關係**；好友卡片下方有幾個固定的快速訊息按鈕（例如「謝謝！」「加油！」），按下後對方收到 App 推播。好友列表的「星號」是前端本機標記，後端不存。

**這一期刻意不含紅利點數互轉**（點數等同貨幣，防詐騙／上限設計份量夠大，留給下一個獨立的 plan）。

## 設計

### `Friendship` model（`app/models.py`，migration `a199303456f7`）

| 欄位 | 說明 |
| --- | --- |
| `user_a_id` / `user_b_id` | 建立時一律正規化成「較小 id、較大 id」，不管誰先發邀請 |
| `requester_id` | 實際發起邀請的人（判斷誰能 accept、推播給誰） |
| `status` | 自由字串：`pending` / `accepted` / `declined`（沒有 DB enum，比照 `Order.status`） |
| `created_at` / `responded_at` | 邀請建立時間／接受或拒絕時間 |

`UniqueConstraint("user_a_id", "user_b_id")`——**同一對使用者在資料庫層面只會有一列**。這是整個設計的核心，順便解掉兩個問題：

- **互相邀請（含雙方同時互掃）的 race condition**：A 邀請 B 已建立 pending，B 再邀請 A 時找到同一列、且 `requester_id` 是對方 → 視為互相同意，直接改成 `accepted`（不用多一次確認）。兩邊真的同時打時，後到的 INSERT 會撞 unique constraint，`IntegrityError` 被接住後改走同一條判斷。
- **拒絕不需要歷史表**：拒絕後保留該列（`declined`），之後任一方重新邀請時，把同一列改回 `pending`、`requester_id` 換成新的發起人。沒有「永久封鎖」的需求，所以不另外記錄拒絕歷史。

`User` 兩個方向的關聯（`friendships_as_a`／`friendships_as_b`）都設 cascade，使用者刪除時整列一併刪除。

### 狀態轉換都用原子性條件式 UPDATE

比照 Coupon 核銷／Product 庫存的慣例：`UPDATE friendships SET status=... WHERE id=:id AND status=:current RETURNING id`，不先 SELECT 再寫回，避免雙擊或多裝置同時操作重複觸發（例如重複接受重複推播）。commit 後不再碰 ORM 物件屬性（避免 `MissingGreenlet`），需要的值 commit 前先存成區域變數，回應時重新查詢。

## 端點（`app/api/v1/endpoints/friends.py`，前綴 `/api/v1/friends`，皆需登入）

| 端點 | 說明 |
| --- | --- |
| `GET /friends` | 已接受的好友，`[{friendship_id, user_id, username, avatar_url, since}]`（資料是「對方」的），新加入的在前 |
| `GET /friends/requests` | 別人寄給我、待我確認的 pending，`[{id (=friendship_id), requester_id, username, avatar_url, created_at}]` |
| `POST /friends/requests` | body `{target_user_id}`，回 **201** + `{id, status, requester_id, user_id, username, avatar_url}`（`user_id` 等是對方的資料） |
| `POST /friends/requests/{id}/accept` | 只有收到邀請的人能呼叫，200 |
| `POST /friends/requests/{id}/reject` | 同上，拒絕不推播對方，200 |
| `POST /friends/{friendship_id}/greetings` | body `{message}`（1–50 字，不驗證內容），200 `{"ok": true}` |

### `POST /friends/requests` 的分支

- `target_user_id` 是自己 → 400；目標使用者不存在 → 404
- 沒有現存列 → 建立 `pending`，推播對方（`friend_request`）
- 現存列 `pending` 且發起人是**對方** → 直接改 `accepted`，推播對方（`friend_request_accepted`）
- 現存列 `pending` 且發起人是自己 → idempotent，回傳現況，不重複推播
- 現存列 `accepted` → **409**，`detail` 為物件 `{"error_code": "already_friends", "message": "已經是好友"}`（比照點數錯誤的 `error_code` 慣例，前端 `detail` 是物件就看 `error_code`）
- 現存列 `declined` → 同一列改回 `pending`、`requester_id` 換成自己，重新推播

### accept / reject

- 不是這對關係的成員、或邀請是自己發的（不能自己接受自己的邀請）→ 404
- 已經是目標狀態 → idempotent 200（不重複推播）；已被轉成另一種狀態（例如已接受再 reject）→ 409

### greetings

只有已接受的好友能送：不是成員 404、還不是好友 409。**不另外建訊息表**——推播本身經 `send_user_push_notifications` 會寫進對方的 `NotificationLog`（App 內通知列表看得到）。標題是「💬 {送出者名稱}」，內文是 `message` 原樣。

## 推播 payload（跟前端對過的契約）

`data` 一律 `{ "type": ..., "screen": "Friends", "friendship_id": <id> }`（比照 `product_restock`／`order_shipped` 帶上物件 id，前端可直接開到那筆）：

| type | 觸發時機 | 收件人 |
| --- | --- | --- |
| `friend_request` | 新邀請／被拒絕後重新邀請 | 被邀請的人 |
| `friend_request_accepted` | 接受邀請／互相邀請自動接受 | 原本的發起人 |
| `friend_greeting` | 送出快速訊息 | 好友 |

推播走 `BackgroundTasks` + `send_user_push_notifications(..., app_id="mynotification")`，commit 之後才觸發。

## 已知限制／刻意的取捨

- **`username` 的來源**：`User.nickname`（2026-09-19 新增，見下）優先；沒填才退回 email 前綴遮罩（前 2 碼 + `***`，例如 `st***`），純 LINE 帳號沒有 email 就是 `用戶{id}`。**刻意不回傳完整 email**（比照先前店員端只給 `user_id` 的隱私考量，避免陌生人透過邀請看到對方信箱）。邏輯全在 `friends.py` 的 `display_name()`，推播文案共用。

- **沒有解除好友（unfriend）端點**——這期需求沒提，之後要加可直接刪除該列（或改成新的 status）。
- **重新邀請沒有冷卻時間**：被拒絕後對方可以立刻再邀請一次。目前規模不需要；如果之後有騷擾問題，再加冷卻或「拒絕 N 次後封鎖」。
- greetings 沒有頻率限制，只限制長度 1–50。
- 沒有為每個使用者產生獨立的 QR token——QR 內容只是公開的 `user_id`，任何人都能對任何 `user_id` 送邀請（對方仍需確認才成立好友）。這是需求（掃了就能加）決定的，如果之後擔心被濫發邀請再加防護。

### `User.nickname`（暱稱）

`users.nickname`（String，nullable，migration `e62e472e4275`，不要求唯一，註冊不強制填）。`GET /users/me` 回傳 `nickname`；`PUT /users/me` 帶 `nickname`：`null`／沒帶＝不改，trim 後空字串或純空白＝清除（存 null），超過 20 字回 422。同一次也移除了沒有作用的 `UserUpdate.username`（`User` model 沒有這個 column，過去 PUT 一直被靜默忽略）。

## 驗證

本機 uvicorn 對正式 Supabase DB 用 6 個拋棄式測試帳號跑過端到端（跑完已清除所有測試資料）：自己加自己 400／不存在 404、建立 pending、重複邀請 idempotent、發起人／第三人 accept 404、accept／重複 accept／已接受再 reject 409、好友列表雙方正確、已是好友 409 `already_friends`、greetings（好友 200／非成員 404／空訊息 422／非好友 409）、互相邀請自動 accepted（同一列）、reject 後重新邀請改回 pending（同一列）、雙方同時互掃只留一列且為 accepted、三種推播的 `NotificationLog.data` 都帶 `type`／`screen`／`friendship_id`。
