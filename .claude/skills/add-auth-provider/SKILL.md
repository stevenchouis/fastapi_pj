---
name: add-auth-provider
description: 新增一種第三方／無密碼登入方式（仿照現有 Google／LINE／Magic Link 登入的 pattern）。使用時機：使用者要求整合新的第三方登入（例如 Apple 登入、Facebook 登入）或其他無密碼登入方式時觸發。
---

# 新增第三方登入方式

此專案的三種第三方登入（Google、LINE、Magic Link，都在
[app/api/v1/endpoints/login.py](../../../app/api/v1/endpoints/login.py)）都遵循同一套
「先用第三方唯一 ID 找帳號，找不到才退而用 Email 找／合併既有帳號，都沒有才新建」的 pattern，
最終都是查到／建立 `User` 後簽發同一套 JWT。新增其他第三方登入時依循以下步驟。

## 前置確認

先跟使用者確認清楚：
- 這個第三方登入方式最終能不能拿到「唯一使用者 ID」與（可選的）Email？
  Email 是否保證已驗證（`email_verified`）？（LINE 預設不給 Email，Google 有
  `email_verified` 欄位可判斷，設計新 provider 時要先弄清楚這點，避免帳號接管風險。）
- 驗證方式是「離線驗證簽章」（像 Google，用官方 SDK 本地驗證 JWT）還是「呼叫官方
  API 驗證」（像 LINE，打 `/oauth2/v2.1/verify`）？兩種在程式碼結構上略有不同
  （google-auth 套件 vs httpx 呼叫），依實際情況選擇。

## 步驟

1. **在 [app/models.py](../../../app/models.py) 的 `User` model 新增識別欄位**
   - 仿照 `google_id` / `line_id`：
     `xxx_id = Column(String, unique=True, index=True, nullable=True)`
   - 加上繁體中文註解說明這是哪個 provider 的唯一識別碼（sub）。
   - 若這個新 provider 也可能不提供 Email，在註解中比照 LINE 的寫法說明清楚。
   - `auth_provider` 欄位不用改 schema，但要記得新 provider 的字串值命名
     （例如 `"apple"`），並想清楚跟既有帳號合併時要不要沿用 `"both"`，還是要
     擴充成更精確的組合值（目前的 `"both"` 只代表 password+google 或
     password+line，混用兩種以上時邏輯需要重新設計，跟使用者確認）。

2. **在 [app/schemas/](../../../app/schemas/) 新增 request schema**
   - 仿照 [app/schemas/line_auth.py](../../../app/schemas/line_auth.py) 或
     [app/schemas/google_auth.py](../../../app/schemas/google_auth.py)：
     一個只含 `id_token: str`（或該 provider 需要的欄位）的 `BaseModel`。
   - 在 `app/schemas/__init__.py` 的 import 與 `__all__` 都要加上去
     （這點跟清單類端點的 schema 不同，登入用的 schema 目前都有正式匯出）。

3. **在 [app/core/config.py](../../../app/core/config.py) 新增設定值**
   - 仿照 `LINE_CHANNEL_ID` / `LINE_CHANNEL_SECRET`：給預設空字串
     `= ""`，避免還沒申請到金鑰前專案無法啟動；呼叫時才因空值報錯。
   - 提醒使用者要在 `.env` 補上對應的值。

4. **在 [app/api/v1/endpoints/login.py](../../../app/api/v1/endpoints/login.py) 新增端點**
   - `@router.post("/login/xxx")`，比照 `login_google` / `login_line` 的結構：
     1. 驗證第三方 token（離線驗證或呼叫官方 API），失敗要 `try/except` 包起來，
        印出 `DEBUG:` 除錯訊息後 `raise HTTPException(status_code=400, ...)`。
     2. 若能拿到 email，且該 provider 有辦法確認已驗證，才信任這個 email
        用來自動合併帳號——不要無條件信任未驗證的 email。
     3. 先用 `xxx_id` 查帳號；找不到才用 email 查、合併（設定
        `user.xxx_id` + `user.auth_provider = "both"`）；都沒有才新建
        `models.User(email=..., xxx_id=..., hashed_password=None,
        auth_provider="xxx", is_active=True)`。
     4. commit／refresh 包在 `try/except`，失敗要 `await db.rollback()`
        並印出 `DEBUG:` 訊息、`raise HTTPException(status_code=500, ...)`。
     5. 檢查 `user.is_active`。
     6. 用 `security.create_access_token(subject=str(user.id), expires_delta=...)`
        簽發跟其他登入方式相同格式的 JWT，回傳
        `{"access_token": ..., "token_type": "bearer"}`。

5. **產生 Alembic migration**（新增了 `xxx_id` 欄位）
   ```powershell
   alembic revision --autogenerate -m "新增 User.xxx_id 欄位"
   alembic upgrade head
   ```
   產生後打開檢查內容再執行 upgrade。

6. **更新 [CLAUDE.md](../../../CLAUDE.md)**
   - 在「驗證機制」段落，比照 Google／LINE 的條列格式，補上新登入方式的
     端點路徑、驗證方式、需要注意的限制（例如是否有 Email、金鑰設定名稱）。

## 完成後檢查清單

- [ ] 未驗證的第三方 email 不會被用來自動合併帳號（避免帳號接管）
- [ ] 新 provider 的識別碼欄位有 `unique=True, index=True, nullable=True`
- [ ] 錯誤處理風格跟同檔案其他端點一致（DEBUG print + try/except + rollback）
- [ ] `.env.example`（若有）或至少提醒使用者要補上新的設定值
- [ ] Alembic migration 已產生並人工檢查過內容
- [ ] CLAUDE.md 的驗證機制段落已更新
