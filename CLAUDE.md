# CLAUDE.md

本檔案為 Claude Code (claude.ai/code) 在此專案中工作時的指引文件。

## 專案概觀

一個以 Poetry 管理、Python >=3.12 的 FastAPI 後端，服務對象是一個 Expo/React Native 行動 App（該 App 不在此 repo 內）。功能涵蓋使用者驗證（JWT）、Expo 推播通知、生日優惠券排程任務，以及檔案上傳。資料庫使用 PostgreSQL（透過 Supabase，走 6543 連線池 Port）。

## 常用指令

依賴套件以 Poetry 管理；請先啟用虛擬環境，或在指令前加上 `poetry run`。

```powershell
# 安裝依賴
poetry install

# 啟動 API（自動重載）。也可用 VS Code 的 launch 設定 "Python Debugger: FastAPI" 啟動。
python -m uvicorn app.main5:app --reload
# 若要讓區域網路內其他裝置（例如同 WiFi 下的手機）連線測試：
python -m uvicorn app.main5:app --host 0.0.0.0 --port 8000 --reload

# Alembic 資料庫遷移（設定檔為 alembic.ini，腳本目錄是 testAlembic/，不是預設的 alembic/）
alembic revision --autogenerate -m "描述文字"
alembic upgrade head
```

目前此專案沒有設定測試框架、linter 或 formatter（ruff 已從開發依賴中移除）。

## 架構說明

**進入點：** `app/main5.py` 是目前實際運行的應用程式（`app.main5:app`）。`app/main.py` 到 `app/main4.py` 是先前的開發版本，留在專案中作為參考／歷史紀錄——不要再擴充這些檔案；若被要求清理專案，優先考慮刪除而非「修好」它們。

**請求流程：** `main5.py` → 掛載 `app.api.v1.api.api_router` 於 `settings.API_V1_STR`（`/api/v1`）→ 各資源的路由分別放在 `app/api/v1/endpoints/`（`login`、`users`、`notifications`、`coupons`、`items`）。

**驗證機制：** 採用 JWT Bearer Token。`app/core/security.py` 負責產生／驗證 Token 及密碼雜湊（透過 passlib 的 argon2/bcrypt）。`app/api/deps.py` 提供 `get_current_user`，會解碼 Token 中的 `sub` 欄位作為 user ID 並查出對應的 `User`——在任何受保護的路由中將它作為 FastAPI 依賴項注入即可。登入端點為 `POST /api/v1/login/access-token`（OAuth2 password form）。

**資料庫——同時存在兩套架構，修改前務必確認自己動到的是哪一套：**

- `app/database_async.py`——目前實際使用中的版本。定義了 `Base`、非同步 engine/session，以及 `get_db` 這個非同步依賴項。**注意：** 其中的 `SQLALCHEMY_DATABASE_URL` 目前是寫死在程式碼中（並未讀取 `settings`／`.env`），且內含 Supabase 的明文連線密碼——請視為敏感資訊；若要修正，可考慮改接 `app/core/config.py` 中已實作好的 `settings.async_database_url`。
- `app/database_sync.py`——同步版本的 SQLAlchemy engine/session，同樣寫死了連線密碼。看起來僅供 Alembic 離線工具使用；實際的 endpoint 程式碼都是走非同步架構。
- `app/models.py` 是從 `database_async` 匯入 `Base`，因此所有 ORM model 都是註冊在非同步版本的 metadata 底下，不論遷移時實際用哪個 engine 執行。
- 設定值（`SECRET_KEY`、`DATABASE_URL`、`ALGORITHM`、Token 過期時間等）透過 `app/core/config.py`（`pydantic-settings`）從 `.env` 讀取，路徑會以專案根目錄為準，不受目前工作目錄影響。

**資料庫遷移：** Alembic 的腳本目錄設定在 `testAlembic/`（並非預設的 `alembic/`，詳見 `alembic.ini` 中的 `script_location`）。`testAlembic/env.py` 會匯入 `app.models`，並使用 `database_async` 的 `Base.metadata` 作為 autogenerate 用的 `target_metadata`。`alembic.ini` 中的 `sqlalchemy.url` 同樣寫死了連線密碼，需注意同上的敏感資訊問題。

**Model 結構**（`app/models.py`）：`User`（含 `email`、`birthday`、`avatar_url`）對 `PushToken`（每個裝置的 Expo 推播 Token）為一對多；對 `NotificationLog`（推播／通知歷史紀錄，含 `is_read` 標記，以及供前端深層連結使用的任意 `data` JSON 欄位）為一對多；對 `Coupon`（生日折扣優惠券，含到期時間）為一對多。所有子關聯皆設定 cascade，使用者刪除時一併刪除。

**排程任務**（`app/services/scheduler_service.py`）：使用 APScheduler 的 `BackgroundScheduler`，於 `main5.py` 的 lifespan handler 中啟動／關閉。`send_birthday_coupons_async_task` 會找出下個月生日的使用者，並發放優惠券與建立通知紀錄；透過獨立的 asyncio event loop（`run_scheduler_bridge`）橋接同步排程器與非同步任務。目前排程為每月 25 日執行一次。

**推播通知**（`app/services/push_service.py`）：`send_user_push_notifications(db_factory, user_id, title, body, data)` 設計為在 FastAPI 的 `BackgroundTask` 中執行——它接收的是 session *工廠函式*（而非 session 本身），這樣才能在觸發請求已經回應完畢後，自行開啟一個短暫的資料庫連線，取完資料後立即關閉，再於資料庫交易之外呼叫 Expo 的 `PushClient` 發送推播。之後若要新增背景推播相關程式碼，建議依循這個模式（先撈資料 → 關閉 DB session → 再發送推播），避免讓資料庫連線在等待網路呼叫時一直開著。

**檔案上傳：** 上傳的檔案存放在專案根目錄的 `uploads/`（不在 `app/` 底下），並透過 `User.avatar_url` 儲存其網址。

## 專案慣例

- 既有程式碼中的註解與 docstring 大量使用繁體中文——修改這些檔案時請延續此慣例。
- 路由程式碼一律使用 `AsyncSession` 搭配 `sqlalchemy` 的 `select`／`update`／`delete`（2.0 風格的查詢寫法），而非舊式的 `Query` API。
- Endpoint 程式碼通常會用 `try/except` 包住 commit 操作，失敗時 rollback，並印出／記錄除錯資訊——修改某個檔案時，請延續該檔案既有的錯誤處理風格，避免在同一模組內混用不同寫法。
