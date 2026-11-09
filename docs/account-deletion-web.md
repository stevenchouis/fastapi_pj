# 網頁刪除帳號申請

2026-11-09 由 mynotification 提出（規格：mynotification repo 的 `plan-account-deletion-web.md`，使用者已確認）。Google Play 資料安全性表單要求提供「不安裝 App 也能申請刪除帳號」的網址。

**正式網址：`https://fastapi-pj-2.onrender.com/account-deletion`**

## 流程

比照 Magic Link，全自動：

1. 使用者在 `/account-deletion` 輸入 Email，按「寄送確認信」。
2. 帳號存在就寄確認信，連結 30 分鐘有效、只能用一次。
3. 點信中連結開啟確認頁 `/account-deletion/confirm?token=…`，頁面顯示遮罩 Email（例如 `st***@hotmail.com`）。
4. 使用者要再按一次「確認刪除帳號」才會真的刪除。這樣信箱的安全掃描機器人預先打開連結也不會刪到帳號，因為開確認頁不會消耗 token。

所有登入方式都可以，只要帳號的 Email 收得到信。沒有 Email 的純 LINE 帳號收不到信，頁面上有提示改在 App 的「設定 → 刪除帳號」操作。

## 頁面（HTML，掛在根路徑，不在 `/api/v1` 底下）

- `GET /account-deletion`：說明會刪除與會保留的資料，加上 Email 表單。頁面是繁體中文、`<meta charset="utf-8">`、手機 viewport、`noindex`，支援深色模式。
- `GET /account-deletion/confirm?token=`：
  - token 有效：顯示遮罩 Email 和確認按鈕。
  - token 無效、過期或已用過：顯示「連結已失效，請重新申請」。
  - 店員帳號：顯示「店員帳號無法自行刪除，請聯絡管理者」，沒有按鈕。

頁面路由在 `app/api/v1/endpoints/account_deletion.py` 的 `page_router`，由 `app/main5.py` 直接掛到根路徑。

## API（頁面用 fetch 呼叫）

- **`POST /api/v1/account-deletion/request`**，body `{email}`
  - 一律回 204，不透露這個 Email 有沒有註冊、有沒有寄信。
  - Email 格式錯誤回 422（FastAPI 預設格式）。
  - 只有 Email 對應到啟用中的帳號（不分大小寫）才會建立 token 並在背景寄信。
  - **頻率限制**：同一個 Email 每分鐘 1 次、24 小時內 5 次，超過也回 204、不寄信。用 `pg_advisory_xact_lock` 讓同一個 Email 的申請排隊，避免兩個同時送出的請求都通過檢查。
- **`POST /api/v1/account-deletion/confirm`**，body `{token}`
  - 204：刪除成功。
  - 400 `{"detail": {"error_code": "invalid_deletion_token", "message": "連結已失效，請重新申請"}}`：token 不存在、過期、已用過，或帳號已經不存在／停用。
  - 403 `{"detail": {"error_code": "staff_account", "message": "店員帳號無法自行刪除，請聯絡管理者"}}`：店員帳號，**不會消耗 token**。
  - token 用一句原子性的 `UPDATE ... WHERE used_at IS NULL` 標記已使用，同一個 token 同時送兩次只會刪一次。

## 資料

新表 `AccountDeletionToken`（`account_deletion_tokens`，migration `d26e7a875d8b`），比照 `MagicLinkToken`：

- `email`：小寫
- `token_hash`：SHA-256，unique
- `expires_at`、`used_at`、`created_at`

只有帳號存在時才建立，所以這張表也拿來算頻率限制。

## 刪除邏輯共用

刪除與匿名化的範圍跟 App 內 `DELETE /users/me` 完全相同。兩邊都呼叫 `app/services/account_service.delete_user_account`，這次從 `users.py` 抽出來，內容沒改，只多清掉這個 Email 的 `AccountDeletionToken`。處理範圍包括：匿名化 `User`、續期證、推播 token、通知、收藏、好友、未使用優惠券、點數明細、簽到、推薦、Magic Link token、訂單上的發票載具。之後要改刪除範圍，只改這一個地方。

## 寄信

`email_service.send_account_deletion_email`，跟 Magic Link 同一組 Brevo 設定。這次把 Brevo 呼叫抽成 `_send_brevo`，Magic Link 信的主旨與內容一字未改。信件連結用 `settings.BASE_URL`（Render 上的環境變數，跟 Magic Link 相同）。

## 驗證（2026-11-09）

用 `httpx.ASGITransport` 打真實端點、連正式 DB，寄信換成假的，外層交易最後 rollback，事後確認測試帳號與 `account_deletion_tokens` 沒有殘留。

- 申請頁：200、有 charset、viewport、刪除與保留說明、LINE 提示
- 申請：
  - 沒註冊的 Email：204、不寄、不建 token
  - 格式錯誤：422
  - 有註冊（大小寫不同也找得到）：寄出 30 分鐘的連結
  - 1 分鐘內再申請：不寄
  - 24 小時內已有 5 次：不寄；最舊那次超過 24 小時後又可以寄
- 確認頁：
  - 顯示遮罩 Email、不出現完整 Email、不消耗 token
  - 無效、空的、夾帶 `<script>` 的 token：顯示失效頁，不會回顯內容
- 店員：確認頁顯示提示、沒有按鈕；POST 403 `staff_account`；帳號沒被刪、token 沒被消耗
- 確認刪除：
  - 204，帳號被匿名化並停用
  - 推播 token、續期證、這個 Email 的刪除 token 都被清掉
  - 同一個 token 再用：400，確認頁顯示失效
  - 已刪除的 Email 再申請：不寄
- 過期 token：POST 400、頁面顯示失效、帳號沒被刪
- 用假的 Brevo 回應驗證：Magic Link 信件內容與原本完全相同；刪除確認信主旨與連結正確
- 回歸：App 內刪除帳號（電子發票腳本）、refresh token、推播 token 清理的驗證腳本都通過

**沒有驗證到的**：真的寄到信箱（要等部署後用真實信箱試）。
