# Google 第三方登入

## 功能概述

使用者可以用 Google 帳號登入 App，不需要另外註冊密碼。前端用 `@react-native-google-signin/google-signin` 取得 Google 的 `id_token`，後端驗證這個 token 後換發本系統自己的 JWT，後續流程（存 Token、打其他 API）都跟原本密碼登入完全一樣。

## 技術流程

```
App                          後端 (FastAPI)                  Google
 |-- Google 原生登入按鈕 ------------------------------------->|
 |<----------------------------------------------- id_token --|
 |-- POST /api/v1/login/google { id_token } -->|
 |                                    |-- 驗證簽章/audience --->|
 |                                    |<---- 使用者資訊(email 等) -|
 |                                    |-- 依 google_id/email 查找或建立帳號
 |<----------- { access_token, token_type } ---|
```

## 後端實作

### 異動檔案

| 檔案 | 異動內容 |
|---|---|
| `app/models.py` | `User` 新增 `google_id`（unique, indexed, nullable）、`auth_provider`（`password` / `google` / `both`）欄位；`hashed_password` 改為 `nullable=True` |
| `app/schemas/google_auth.py` | 新增 `GoogleLoginRequest`（`id_token: str`） |
| `app/api/v1/endpoints/login.py` | 新增 `POST /login/google` |
| `app/core/config.py` | 新增 `GOOGLE_CLIENT_ID` 設定 |
| `testAlembic/versions/55ebc3401a27_*.py` | 新增 `google_id`、`auth_provider` 欄位的 migration |
| `pyproject.toml` | 新增 `google-auth` 依賴 |

### API 規格

**POST `/api/v1/login/google`**

Request:
```json
{ "id_token": "<Google 回傳的 id_token>" }
```

Response（與 `/login/access-token` 格式一致）：
```json
{ "access_token": "...", "token_type": "bearer" }
```

錯誤情況：
- `400`：id_token 驗證失敗（簽章錯誤、audience 不符、過期）
- `400`：Google 帳號的 email 尚未通過驗證（`email_verified != true`）
- `400`：帳號已被停用

### 業務邏輯

1. 用 `google.oauth2.id_token.verify_oauth2_token` 離線驗證 id_token 的簽章、有效期限、`audience`（比對 `GOOGLE_CLIENT_ID`）
2. 檢查 Google 回傳的 `email_verified` 必須為 `true`，才信任這個 email（避免帳號接管風險）
3. 先用 `google_id`（Google 的 `sub`）查找是否已經登入過
4. 若沒有，改用 `email` 查找既有帳號：
   - 找到 → 自動補綁定 `google_id`，`auth_provider` 改為 `both`
   - 沒找到 → 建立新帳號，`hashed_password=NULL`，`auth_provider='google'`
5. 檢查帳號是否啟用，簽發跟密碼登入一樣的 JWT

### 為什麼用 `google-auth` 套件而不是呼叫 tokeninfo endpoint

`google.oauth2.id_token.verify_oauth2_token` 是離線驗證（用快取的 Google 公鑰驗證簽章），比呼叫 Google 的 `https://oauth2.googleapis.com/tokeninfo` 少一次對外 HTTP request，也不受該 endpoint 的流量限制影響。

## 使用者需要做的事：申請 Google OAuth 用戶端 ID

後端驗證 `id_token` 需要知道自己的 App 對應哪個 Google OAuth 用戶端（`audience`），前端的 Google 登入按鈕也需要對應的用戶端 ID 才能運作。這一步無法由 AI 代為操作，需要您本人到 Google Cloud Console 設定。

### Step by step

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)，用您的 Google 帳號登入
2. 建立一個新專案（或選擇既有專案）
3. 左側選單 → **API 和服務** → **OAuth 同意畫面**，設定應用程式名稱、支援電子郵件等基本資訊（開發階段選「測試」狀態即可，不用送審）
4. 左側選單 → **API 和服務** → **憑證** → **建立憑證** → **OAuth 用戶端 ID**，需要建立**三組**：
   - **Web 應用程式**：這組給**後端**驗證 `id_token` 的 `audience` 用（也就是 `GOOGLE_CLIENT_ID`）
   - **iOS**：Bundle ID 要跟 App 的 `app.json` / `app.config.js` 裡的 iOS bundle identifier 一致，給**前端**設定用
   - **Android**：Package name + SHA-1 憑證指紋（要跟 App 的 Android package 一致；SHA-1 可透過 `eas credentials` 或 EAS Build 取得），給**前端**設定用
5. 建立完成後，把三組 Client ID 分別交給對應的一邊：
   - Web Client ID → 給**後端**（我），設進 `.env` 的 `GOOGLE_CLIENT_ID`
   - iOS / Android Client ID → 給**前端**，設進前端專案的環境變數

> 目前狀態：Web Client ID 已提供並設定完成；iOS Client ID 已提供給前端；Android Client ID 待您透過 EAS 取得 SHA-1 後補上。

## 環境變數

```env
# .env
GOOGLE_CLIENT_ID=<Web Client ID>
```

## 安全性設計考量

- **只信任已驗證的 email**：綁定既有帳號前檢查 `email_verified`，避免理論上的帳號接管風險（例如尚未驗證 email 就被人拿去綁定別人的帳號）
- **audience 檢查**：驗證時明確比對 `GOOGLE_CLIENT_ID`，避免拿到「給別的 App 簽發」的 id_token 也能登入
- **`hashed_password` 允許為空**：純 Google 帳號沒有密碼，`/login/access-token`（密碼登入）遇到這種帳號會因為 `verify_password` 對 `None` 雜湊比對失敗而正常拒絕，不會意外放行

## 測試狀態

已由前端完成實機整合測試：既有帳號（`stevenchouis@gmail.com`）成功自動綁定 `google_id`，`auth_provider` 更新為 `both`，`POST /api/v1/login/google` 回傳 `200 OK` 並取得可用的 JWT。
