# 錯誤監控（Sentry）

2026-11-09 由 mynotification 提出，使用者已確認。起因是同一天抓到的排程 bug：排程在正式環境一直失敗，但只印 log，沒人知道。sentry.io 組織 `kc-1n`，後端專案 `fastapi-pj`（App 端 mynotification 另有自己的專案）。

## 設定（Render Environment）

| 變數 | 值 | 說明 |
|---|---|---|
| `SENTRY_DSN` | 使用者在 Sentry 專案設定頁取得的 DSN | **沒設就完全不啟用**（本機開發、驗證腳本不回報） |
| `SENTRY_ENVIRONMENT` | 選填 | 沒設時：在 Render 上是 `production`（Render 會自動帶 `RENDER` 環境變數），其他地方是 `local` |

DSN 只放在 Render 環境變數，不進 repo。

## 程式

- `app/core/monitoring.py`：`init_sentry()`（`main5.py` 在建立 app 之前呼叫）、`capture_exception(exc, **tags)`、`capture_message(msg, **tags)`。Sentry 沒啟用時後兩個什麼都不做，可以放心在任何地方呼叫。
- 初始化參數：`send_default_pii=False`、`traces_sample_rate=0`（不做效能追蹤）、`max_request_body_size="never"`、`include_local_variables=False`，並關閉 logging 整合。

## 回報範圍

| 來源 | 怎麼抓 |
|---|---|
| API 未處理的例外、回 5xx 的請求（包括我們自己 `raise HTTPException(500)`） | FastAPI／Starlette 整合預設行為 |
| 排程往外丟的例外 | `scheduler_service._on_job_error` 監聽 APScheduler 的 `EVENT_JOB_ERROR`。排程在別的執行緒跑，APScheduler 接住後只會寫 log，所以要靠這個監聽器 |
| 排程內部自己 `try/except` 吞掉的例外 | 各排程的 except 區塊明確 `capture_exception(e, job=...)`：生日禮券、點數到期、到期提醒（含各自的推播）、推播 receipt 檢查、過期 token 清除。這次的 bug 就是被這種 except 吞掉的 |
| 推播 | 發送時的非預期例外、存 ticket 失敗、Expo 回 `DeviceNotRegistered` 以外的錯誤（例如 `InvalidCredentials`、`MessageTooBig`）、receipt 查詢或套用失敗。`DeviceNotRegistered` 是預期中的情況，不送 |
| 寄信（Magic Link、刪除帳號確認信） | Brevo 回非 2xx（只送狀態碼）、連線例外、Brevo 尚未設定 |

保活 ping 失敗不送，因為偶發的網路問題會洗版。logging 不轉成 Sentry 事件，原因是現有 log 會帶 Email、token 片段，而且很多預期中的錯誤也用 `logger.error` 記。

## 個資

- request：不送 body、cookie、query string（Magic Link／刪除帳號連結的 token 在這裡）、URL 的 `?` 之後。header 只留白名單：`user-agent`、`content-type`、`content-length`、`host`、`accept`、`x-forwarded-for`，所以 `Authorization`、`X-Admin-Key` 都不送。
- 不送 stack frame 的區域變數。
- 使用者只帶 id：`deps.get_current_user` 會 `set_user({"id": ...})`，`before_send` 也會再把 user 欄位裁到只剩 id。
- 所有事件與麵包屑的文字再過一次 `_scrub_text`，遮掉：Email、`ExponentPushToken[...]`、SQLAlchemy 錯誤訊息裡的 `[parameters: ...]`、`token=`／`password=`／`api-key=` 這類字串。

## 驗證方法（部署並設好 `SENTRY_DSN` 之後）

測試端點 `POST /api/v1/monitoring/test-error`，需要 `X-Admin-Key`：

- 不帶參數：API 直接丟 `SentryTestError`，回 500。用來驗證 API 例外會被回報。
- `?source=scheduler`：排一個 1 秒後執行、一定會失敗的一次性排程，回 202。用來驗證排程執行緒的例外會被回報（tag `job=sentry-test-error`）。

Sentry 後台 Issues 應該會出現兩筆 `SentryTestError`，environment 是 `production`。端點保留著，之後要再確認監控是否正常時可以重用。

## 本機驗證（2026-11-09）

把 Sentry transport 換成記憶體收集，不會真的送出：

- 沒設 DSN 不啟用；設了才啟用，environment 是 `local`
- 測試端點錯的 admin key 回 401
- API 例外被回報，類型正確，沒有 stack frame 變數。請求時刻意帶了 Bearer JWT、`?token=`、cookie、body 裡的密碼和 Email、admin key，事件裡全部找不到；header 只剩白名單
- `?source=scheduler`：排程執行緒的例外被回報，帶 `job` tag
- `capture_exception` 的 tag 正確，Email 和 push token 被遮罩
- Brevo 回 401 時只送狀態碼，不含收件人 Email 和連結 token
- user 只剩 id（email、ip 都被拿掉）
- SQL 參數被遮罩
- 回歸：推播 token 清理、刪除帳號網頁、寄信內容的驗證腳本都通過
