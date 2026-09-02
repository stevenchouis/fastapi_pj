# Magic Link（Email 連結）登入

## 功能概述

使用者只要輸入 email，就會收到一封含登入連結的信，點擊連結後 App 會被喚醒並自動完成登入，全程不需要密碼。

## 技術流程

```
App                     後端 (FastAPI)                Resend              Email App
 |-- POST /login/magic-link/request { email } -->|
 |                              |-- 產生一次性 token（存 hash）
 |                              |-- 寄信（BackgroundTask） ---->|
 |                              |                                寄出 ------>|
 |<-------- 204 No Content -----|
 |                                                                使用者點連結
 |                                                          GET /login/magic-link/redirect?token=xxx
 |                                                          （後端回傳 HTML，轉跳 mynotification://magic-login?token=xxx）
 |<===== 深連結喚醒 App，帶著 token =====|
 |-- POST /login/magic-link/verify { token } ---->|
 |                              |-- 原子性標記 token 已使用
 |                              |-- 依 email 查找或建立帳號
 |<---- { access_token, token_type } -------------|
```

## 後端實作

### 異動檔案

| 檔案 | 異動內容 |
|---|---|
| `app/models.py` | 新增 `MagicLinkToken`（`email`、`token_hash`、`expires_at`、`used_at`、`created_at`） |
| `app/schemas/magic_link.py` | 新增 `MagicLinkRequest`（`email`）、`MagicLinkVerify`（`token`） |
| `app/services/email_service.py` | 新增 `send_magic_link_email`，用 Resend 寄信 |
| `app/api/v1/endpoints/login.py` | 新增 `POST /login/magic-link/request`、`GET /login/magic-link/redirect`、`POST /login/magic-link/verify` |
| `app/core/config.py` | 新增 `RESEND_API_KEY`、`BASE_URL` 設定 |
| `testAlembic/versions/9413b82a0f95_*.py` | 新增 `magic_link_tokens` 表的 migration |
| `pyproject.toml` | 新增 `resend` 依賴 |

### API 規格

**POST `/api/v1/login/magic-link/request`**

Request:
```json
{ "email": "user@example.com" }
```

Response：`204 No Content`（不管 email 是否存在、是否觸發 cooldown，一律回傳相同結果，避免帳號枚舉）

**GET `/api/v1/login/magic-link/redirect?token=xxx`**

信件連結指向這個 endpoint，回傳一小段 HTML，用 meta-refresh + JS 轉跳到 `mynotification://magic-login?token=xxx` 喚醒 App。**這一步不會消耗 token**（原因見下方安全性設計）。

**POST `/api/v1/login/magic-link/verify`**

Request:
```json
{ "token": "<深連結帶的 token>" }
```

Response（與其他登入 API 格式一致）：
```json
{ "access_token": "...", "token_type": "bearer" }
```

錯誤情況：
- `400`：token 無效、已使用過、或已過期（15 分鐘）
- `400`：帳號已被停用

### 業務邏輯

1. `/request`：檢查 60 秒內是否已寄過信（cooldown，防止灌信騷擾）；沒有的話產生一組高熵亂數 token（`secrets.token_urlsafe(32)`），只把它的 SHA-256 hash 存進資料庫，15 分鐘後過期，用 `BackgroundTask` 寄信（不阻塞 API 回應）
2. `/redirect`：純轉跳，不驗證也不消耗 token
3. `/verify`：用一次 `UPDATE ... WHERE used_at IS NULL AND expires_at > now()` 的原子操作標記 token 已使用；成功才繼續查找/建立帳號、簽發 JWT

### 帳號建立政策

跟 Google 登入不同，Magic Link 純粹用 email 收信驗證身份，不需要額外的綁定判斷：
- email 不存在 → 建立新帳號，`auth_provider='magic_link'`，`hashed_password=NULL`
- email 已存在（不管原本是 `password`／`google`／`both`）→ 直接登入，不會去改動既有的 `auth_provider`

## 使用者需要做的事：申請 Resend API Key

寄信需要一個寄信服務，這個專案原本完全沒有設定任何寄信服務，需要您本人申請。

### Step by step

1. 前往 [resend.com](https://resend.com/) 註冊帳號（可用 GitHub 帳號快速註冊）
2. 登入後，左側選單 → **API Keys** → **Create API Key**，權限給 **Sending access** 即可
3. 複製產生的 API Key（只會顯示一次），交給我設進 `.env` 的 `RESEND_API_KEY`

> ⚠️ **Sandbox 模式限制**：在您驗證自己的網域之前，Resend 只允許寄信給**您註冊 Resend 帳號時用的那個 email**，寄給其他地址會失敗。開發階段用您自己的帳號測試沒問題；如果之後要開放給其他人用 Magic Link 登入，需要額外去 Resend 後台驗證一個網域（加 DNS 記錄）。

## 環境變數

```env
# .env
RESEND_API_KEY=<Resend API Key>
# Magic Link 落地頁的對外網址，要跟 App 端 EXPO_PUBLIC_API_URL 用同一台主機/IP
BASE_URL=http://192.168.68.56:8000
```

**注意**：`BASE_URL` 目前寫死這台開發機當下的區網 IP。如果之後這台電腦換了 WiFi、或路由器重新配發 IP（DHCP），這個值就會過期，導致信件裡的連結打不通，需要手動更新 `.env` 並重啟 server。

## 安全性設計考量

- **只存 token 的 hash**：資料庫的 `token_hash` 是 SHA-256 雜湊值，不存明文 token，即使資料庫外洩也無法重放
- **延後到 `/verify` 才消耗 token**：`/redirect` 只負責轉跳，不驗證也不標記使用。這是為了避免 Outlook/Gmail 等信箱的「安全連結掃描」機器人自動預先擷取信件裡的連結，如果掃描這一步就把 token 用掉，使用者自己點擊時 token 早就失效了
- **原子性標記已使用**：`/verify` 用單一 `UPDATE ... WHERE used_at IS NULL` 語句標記，避免同一個 token 被同時打兩次 verify 時重複發放登入（race condition）
- **一律回傳相同結果**：`/request` 不管 email 存不存在，回應都一樣，避免被拿來枚舉系統裡有哪些已註冊的 email
- **60 秒 cooldown**：同一個 email 短時間內重複請求不會真的重寄信，降低被拿來對別人信箱灌信騷擾的風險

## 已知限制 / 待辦

- Resend sandbox 模式下只能寄給您自己的信箱（見上方申請步驟的提醒），要開放給其他使用者測試前需要驗證網域
- `BASE_URL` 是寫死的區網 IP，換網路環境要記得更新
- Android 深連結（`mynotification://`）需要前端的 Expo Router 有正確設定對應的 scheme，這部分由前端負責
