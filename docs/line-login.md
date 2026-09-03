# LINE 第三方登入

## 功能概述

使用者可以用 LINE 帳號登入 App，不需要另外註冊密碼。目前（2026-09-04 起）採用 LINE 原生 SDK（`@xmartlabs/react-native-line`）：前端呼叫 `Line.login()` 直接拿到 `id_token`，後端驗證這個 token 後換發本系統自己的 JWT，流程跟 Google 登入完全對稱。

**這支功能曾經歷一次架構調整**，先前（2026-09-03）是走瀏覽器版 OAuth（authorization code + 後端中繼落地頁），後來前端改用原生 SDK 後整段 code 交換流程都拿掉了。舊流程的除錯過程也記錄在下方「除錯歷程」，因為裡面踩過的坑（LINE 內建瀏覽器對自訂 scheme 支援不完整、Expo Router 的路徑解析規則）之後如果有其他類似的「App 內建瀏覽器 + 自訂 scheme 轉跳」需求還是用得到。

## 目前的技術流程（原生 SDK，2026-09-04 起）

```
App                              後端 (FastAPI)                  LINE
 |-- Line.login() 原生登入 ---------------------------------->|
 |<---------------------------------------- id_token, userProfile --|
 |-- POST /api/v1/login/line { id_token } -->|
 |                                  |-- POST /oauth2/v2.1/verify (id_token, client_id) --->|
 |                                  |<---------------------- 解碼後的 claims (sub 等) --|
 |                                  |-- 依 line_id/email 查找或建立帳號
 |<----------- { access_token, token_type } ---|
```

## 後端實作

### 異動檔案

| 檔案 | 異動內容 |
|---|---|
| `app/models.py` | `User` 新增 `line_id`（unique, indexed, nullable）欄位；`auth_provider` 多一個 `'line'` 值 |
| `app/schemas/line_auth.py` | `LineLoginRequest`（目前只有 `id_token: str`） |
| `app/core/config.py` | 新增 `LINE_CHANNEL_ID`、`LINE_CHANNEL_SECRET` 設定（皆有預設值 `""`，取得前不會讓服務啟動失敗） |
| `app/api/v1/endpoints/login.py` | 新增 `POST /login/line`；`GET /login/line/redirect` 是舊流程留下的端點，見下方「已知限制／待辦」 |
| `testAlembic/versions/c84194933dd3_*.py` | 新增 `users.line_id` 欄位的 migration |

### API 規格

**POST `/api/v1/login/line`**

Request:
```json
{ "id_token": "<LINE 原生 SDK 登入後回傳的 id_token>" }
```

Response（與其他登入 API 格式一致）：
```json
{ "access_token": "...", "token_type": "bearer" }
```

錯誤情況：
- `400`：`id_token` 驗證失敗（簽章錯誤、audience 不符、過期、`LINE_CHANNEL_ID`/`LINE_CHANNEL_SECRET` 設定錯誤）
- `400`：帳號已被停用

**GET `/api/v1/login/line/redirect`**（舊流程遺留，目前前端不會呼叫）

### 業務邏輯

1. 把前端傳來的 `id_token` 原封不動送給 LINE 的 `POST https://api.line.me/oauth2/v2.1/verify`（帶 `client_id`），由 LINE 官方驗證簽章與 audience，換回解碼後的 claims
2. `line_id`（LINE 的 `sub`）跟既有的 Google 登入邏輯對稱：先用 `line_id` 找帳號；找不到才退而用 `email`（若 LINE 有給）找既有帳號自動補綁定；都沒有才新建帳號
3. **LINE 預設只給 `sub`／暱稱，不含 Email**（要拿 Email 需另外向 LINE 官方申請 `Email address permission`，會有審核時間），所以純 LINE 帳號的 `email` 允許為 `null`
4. 檢查帳號是否啟用，簽發跟其他登入方式一樣的 JWT

### 為什麼用 LINE 的 `/verify` 端點而不是自己驗證 JWT 簽章

LINE 的 id_token 是標準 JWT，理論上可以自己用 LINE 的 JWKS（`/oauth2/v2.1/certs`）在本地驗證簽章（類似 Google 登入用 `google-auth` 套件離線驗證的做法）。但這個專案目前選擇直接呼叫 LINE 官方的 `/oauth2/v2.1/verify` 端點做驗證，理由：
- 少一套 JWKS 抓取/快取/`kid` 比對的實作與依賴
- 這支端點是 LINE 官方文件推薦的輕量驗證方式，行為跟本地驗證等價（LINE 伺服器本來就是簽發者，驗證結果一定正確）
- 唯一代價是多一次對外 HTTP request，對登入這種低頻操作可以接受

## 使用者需要做的事：申請 LINE Login Channel

後端驗證 `id_token` 需要知道自己的 App 對應哪個 LINE Channel（`LINE_CHANNEL_ID`/`LINE_CHANNEL_SECRET`），這一步無法由 AI 代為操作，需要您本人到 LINE Developers Console 設定。

### Step by step

1. 前往 [LINE Developers Console](https://developers.line.biz/console/)，用您的 LINE 帳號登入
2. 建立一個 Provider（如果還沒有），再建立一個 **LINE Login** 類型的 Channel
3. Channel 建立後，在 **Basic settings** 分頁可以看到：
   - **Channel ID**：公開值，交給後端（我）設進 `.env` 的 `LINE_CHANNEL_ID`，前端 App 端也會用到
   - **Channel Secret**：機密值，**不要透過聊天訊息或跨 session 訊息傳遞**，用其他安全管道（例如直接在對話裡貼給我、或後台環境變數畫面直接輸入）交給後端設進 `.env` 的 `LINE_CHANNEL_SECRET`
4. 若前端之後改回瀏覽器版 OAuth（目前已不需要），才需要在 **LINE Login** 分頁設定 Callback URL；原生 SDK 流程不需要註冊 Callback URL

> 目前狀態：Channel ID（`2011410702`）與 Channel Secret 皆已提供並設進本機 `.env` 與 Render 環境變數。

## 環境變數

```env
# .env
LINE_CHANNEL_ID=<Channel ID，公開值>
LINE_CHANNEL_SECRET=<Channel Secret，機密值，不要外流>
```

**Render 部署注意**：這兩個環境變數要另外在 Render 後台的 Environment 分頁設定，跟本機 `.env` 是分開管理的，不會因為 `git push` 自動同步過去。

## 安全性設計考量

- **audience 檢查**：`/verify` 呼叫時帶 `client_id`，LINE 會確認這個 `id_token` 是簽發給這個 Channel 的，避免拿到「給別的 App 簽發」的 token 也能登入
- **Channel Secret 不進版控**：只存在 `.env`（已在 `.gitignore`），透過安全管道（非跨 session 訊息內容）取得
- **email 允許為 null**：純 LINE 帳號沒有 email 時，`hashed_password` 也是 `NULL`，跟 Google/Magic Link 帳號一樣，`/login/access-token`（密碼登入）不會意外放行

## 除錯歷程（舊版瀏覽器 OAuth 流程，僅供歷史參考）

這段記錄的是 2026-09-03 舊流程（authorization code + 後端中繼落地頁）上線時踩過的坑，改用原生 SDK 後已不再適用，但踩坑經驗值得保留：

1. **LINE 內建瀏覽器（LIFF WebView）對 HTTP 302 轉跳到非 http(s) 自訂 scheme 支援不完整**：一開始 `GET /login/line/redirect` 單純回 302 到 `mynotification://redirect?...`，在系統瀏覽器/Chrome Custom Tabs 正常，但使用者透過已安裝的 LINE App 內建瀏覽器授權時會卡住、觸發重試迴圈。解法：改回傳 HTML，用 `<meta http-equiv="refresh">` + `<script>location.replace(...)</script>` + 備用可點擊連結（跟 Magic Link 落地頁同一套做法），對各種內嵌瀏覽器相容性較好。
2. **`mynotification://redirect`（兩斜線）vs `mynotification:///redirect`（三斜線）**：兩斜線寫法下 URL 解析規則會把 `redirect` 當成 host 而不是 path，但 Expo Router 是照 path 比對路由，導致比對不到、跳到「Unmatched Route」畫面。改成三斜線才正確解析成 path。
3. **一場誤導性的除錯**：改完前兩點後 `POST /login/line`（舊版 code 交換流程）持續回 400，一路懷疑 `redirect_uri` 不一致、Channel Secret 錯誤、Render 重新部署時機等，最後真正原因是**使用者本機終端機殘留一個手動設定過的 `EXPO_PUBLIC_API_URL` 環境變數**，蓋掉了 `.env.local` 的設定，導致 App 全程打的是本機開發後端而不是 Render 正式環境——而本機剛好也有實作同一支登入且回傳一模一樣的錯誤訊息，才會一直誤導成正式環境的邏輯問題。**教訓：** 前端回報「後端回某個特定錯誤字串」時，值得先確認 App 實際打的網域是不是預期的那個，不要預設一定是打到正式環境。

## 已知限制 / 待辦

- `GET /api/v1/login/line/redirect` 是舊流程留下的端點，改用原生 SDK 後前端已經不會再呼叫，但暫時保留（放著沒有維運成本），確認完全沒有依賴後可以整支移除
- 目前沒有 Email 權限（LINE Email address permission 尚未申請），純 LINE 帳號無法用 email 找回/合併帳號，也無法透過 `/coupons/admin/issue`（用 email 手動發券）鎖定
