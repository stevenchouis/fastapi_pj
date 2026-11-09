# 登入自動續期（Refresh Token）

2026-11-09 由 mynotification 提出（規格：mynotification repo 的 `plan-refresh-token.md`，使用者已確認）。顧客 App（mynotification）與店員 App（staff-scanner）都會接。

## 兩種憑證

| | 通行證（access token） | 續期證（refresh token） |
|---|---|---|
| 格式 | JWT（`sub`＝user id） | `secrets.token_urlsafe(48)` 隨機字串，**不是 JWT** |
| 效期 | `ACCESS_TOKEN_EXPIRE_MINUTES`（預設與 `.env` 都是 30 分鐘），customer／staff 相同，這次沒改 | customer 30 天、staff 7 天（依**發證當下**的角色），每次換證重新起算 |
| 存放 | 不存 DB | 新表 `refresh_tokens`（migration `6f14b35c9119`）只存 SHA-256 雜湊 |

常數在 `app/services/auth_token_service.py`（`REFRESH_TTL_BY_ROLE`、`REUSE_GRACE`），整個功能的邏輯都在這支。

## API

- **四個登入端點**（`/login/access-token`、`/login/google`、`/login/line`、`/login/magic-link/verify`）回應多一個欄位：`{access_token, token_type, refresh_token}`。每次登入發一張新的續期證（同一帳號在多台裝置登入各有各的，互不影響）。
- **`POST /api/v1/login/refresh`**（不需 Authorization），body `{refresh_token}`
  - 200：`{access_token, token_type, refresh_token: <新的>}`，舊的那張作廢（rotation）
  - 401：`{"detail": {"error_code": "invalid_refresh_token", "message": "登入已過期，請重新登入"}}`，情況包括：不存在、過期、已作廢（登出／偷用偵測）、帳號已刪除或停用、角色跟發證當下不同
  - body 缺欄位：422（FastAPI 預設格式）
- **`POST /api/v1/logout`**（不需 Authorization），body `{refresh_token}` → 一律 204（找不到也是）。只作廢這一張；已發出的通行證不會被撤銷，最多 30 分鐘後自然失效。

舊版 App 不送不收 `refresh_token`，行為完全不變（多出來的回應欄位被忽略）。

## Rotation、30 秒寬限、偷用偵測

`refresh_tokens` 欄位：`user_id`（FK，`ON DELETE CASCADE`）、`token_hash`（unique）、`role`、`expires_at`、`rotated_at`、`revoked_at`、`created_at`。

1. 換證時用一句原子性的 `UPDATE ... SET rotated_at=now WHERE token_hash=? AND rotated_at IS NULL AND revoked_at IS NULL AND expires_at>now RETURNING`。同一張同時送兩次，只有一個請求會成功。
2. UPDATE 沒命中的話，再查這張的狀態：
   - **已換過證，換證時間在 30 秒內**（網路重送、兩個請求同時送出）：**再發一組新的**（不是同一組；DB 只存雜湊，拿不回原本那組的明文）。先前那組也仍有效，兩組都能繼續用到過期。
   - **已換過證，超過 30 秒**：視為被偷用，作廢這個使用者**全部**續期證（所有裝置都要重新登入），回 401。
   - 不存在、過期、已作廢：回 401。
3. 角色或帳號狀態不符時也會作廢該使用者全部續期證。這時 `role` 跟目前的 `User.role` 不同（角色都是直接改 DB，沒有端點），或者 `is_active=False`。

被作廢（`revoked_at`）的續期證不適用寬限，所以偷用偵測之後連寬限內的重送也會失敗。

**清理**：每次發新續期證時，順便刪掉該使用者已過期的列。沒有另外的排程。

## 刪除帳號／角色變更

- `DELETE /users/me`：刪除該使用者全部 `refresh_tokens` 列。
- 角色變更：沒有變更角色的端點，靠換證時比對 `role` 擋下（見上）。改完角色後，該使用者下次換證會 401，必須重新登入，新角色的效期才會生效。

## 前端注意

- 通行證過期時，受保護端點回 **401**（`get_current_user` 的 `detail` 是字串，例如「無法驗證憑證」）。前端收到 401 就打 `/login/refresh`，換到新的再重送原本的請求；`/login/refresh` 也回 401 就回登入頁。
- 每次換證都要把**新的** `refresh_token` 存起來蓋掉舊的。一直拿舊的去換，超過 30 秒就會觸發偷用偵測，所有裝置都會被登出。
- 同時有多個請求都 401 時，最好只打一次 refresh。後端有 30 秒寬限，所以同時打兩次不會被當成偷用。

## 實機測試：讓通行證 1 分鐘就過期

`app/core/config.py` 有兩個設定：

- `SHORT_ACCESS_TOKEN_USER_IDS`：逗號分隔的 user id，預設空字串。
- `SHORT_ACCESS_TOKEN_MINUTES`：預設 1。

列在 `SHORT_ACCESS_TOKEN_USER_IDS` 的帳號，之後**新發**的通行證（登入和換證）都只有 1 分鐘效期，其他帳號不受影響。

做法：

1. 到 Render 的 Environment 新增 `SHORT_ACCESS_TOKEN_USER_IDS=<測試帳號 id>`（多個用逗號），儲存後 Render 會重新部署。
2. 用該帳號在 App 重新登入，等超過 1 分鐘再操作任何需要登入的畫面，應該無感續期。
3. 測完把 Render 上這個環境變數刪掉或清空。

## 驗證（2026-11-09）

用 `httpx.ASGITransport` 打真實端點、連正式 DB，外層交易最後 rollback，事後確認 `refresh_tokens` 與測試帳號都沒有殘留。

- 密碼登入回三個欄位，通行證約 30 分鐘、續期證 customer 30 天
- 換證成功、新通行證可用、新續期證效期重新起算
- 30 秒內重送舊續期證 → 200 再發一組，先前那組仍能換證
- 把換證時間改成 31 秒前再用 → 401，該使用者全部續期證（含寬限發出的）都作廢
- 登出後換證 401、登出不存在的 204、不存在的 401、缺 body 422
- 過期 401，下次登入時過期列被清掉
- 角色從 customer 改 staff → 舊續期證 401；重新登入後 staff 續期證 7 天，換證後仍 7 天
- `is_active=False` → 401
- 刪除帳號後換證 401，DB 沒有該使用者的續期證
- `SHORT_ACCESS_TOKEN_USER_IDS` 內的帳號登入／換證拿到 1 分鐘通行證，其他帳號仍 30 分鐘
- Magic Link、Google（mock 驗證）、LINE（mock 驗證）登入都回 `refresh_token`，且可換證
- 回歸：電子發票載具（含刪除帳號）、開屏彈窗的驗證腳本都通過

**沒有驗證到的**：前端實機；Google／LINE 的真實 id_token（驗證邏輯沒改，只換了回應）。
