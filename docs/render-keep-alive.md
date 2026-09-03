# Render 免費方案保活機制

## 功能概述

這個服務部署在 Render.com 的免費方案（Free tier）Web Service，Render 免費方案會在**閒置 15 分鐘後自動休眠**，下一個請求進來要花 30-60 秒冷啟動。為了讓 App 使用時不會遇到這個延遲，加了一個定時自我 ping 的排程任務，讓服務在有人使用的期間維持醒著。

## 技術流程

```
APScheduler (每 14 分鐘)
    |
    |-- GET {BASE_URL}/health -->  自己這個服務
    |<---------- 200 { status: "alive", version: "..." } --|
```

## 後端實作

### 異動檔案

| 檔案 | 異動內容 |
|---|---|
| `app/services/scheduler_service.py` | 新增 `self_ping_task`（打 `{BASE_URL}/health`）、`run_self_ping_bridge`（橋接同步 `BackgroundScheduler` 與這個 async task），並在 `start_scheduler()` 註冊一個 `interval` 排程，每 14 分鐘執行一次 |
| `app/main5.py` | `/health` 的 `version` 欄位改成 `1.0.2`（純粹用來驗證 Render 部署有真的套用到最新程式碼，見下方「為什麼要有 version 欄位」） |

### 為什麼間隔是 14 分鐘

Render 免費方案的休眠閾值是 15 分鐘無流量。間隔設在略短於這個閾值（14 分鐘），確保下一次 ping 一定會在服務被判定閒置之前送達，避免服務真的睡著。

**注意：** 只要 ping 間隔小於 15 分鐘，服務就會維持 24 小時醒著，這會用掉 Render 免費方案「每個 workspace 每月 750 小時」額度中的大部分（一個月最多 744 小時，24/7 保活大概會用掉 672~744 小時，取決於當月天數），只剩下不到 10~80 小時的緩衝。如果同一個 Render workspace 之後還有其他免費服務也在跑，額度是共用加總計算的，需要留意加總是否會超過 750 小時上限（超過會被暫停到下個月）。目前這個 workspace 只有這一個免費服務，可以接受。

### 為什麼要有 `version` 欄位

`/health` 的 `version` 欄位純粹是為了讓「這次改動有沒有真的部署到 Render」這件事可以從外部驗證。像保活 job 這種只影響 server 內部行為（不改變任何 HTTP 回應內容）的改動，光是打 API 看回應完全看不出新舊版差異，過去在除錯 LINE 登入問題時就因為驗證方式不夠嚴謹而誤判過部署狀態（詳見 `docs/line-login.md` 的除錯歷程）。之後如果又要做這類「內部邏輯改動、外部行為不變」的修改，記得比照這個做法，找一個外部可觀察的訊號來確認部署完成，不要只用「打某支 API 看回應字串有沒有出現」這種可能兩個版本都會通過的檢查方式。

## 驗證方式

1. 本機直接呼叫 `self_ping_task()`，確認能正確打到 `{BASE_URL}/health` 並拿到 200
2. 本機啟動 `scheduler`，用 `scheduler.get_jobs()` 確認保活 job 有註冊、`next_run_time` 是 14 分鐘後
3. 部署到 Render 後，透過 `/health` 回傳的 `version` 從 `1.0.1` 變成 `1.0.2` 確認新版程式碼（含保活 job）已經上線

**尚未做的驗證：** 長時間（15-30 分鐘以上）觀察 Render 服務是否真的沒有進入休眠、沒有冷啟動延遲，這需要之後在正常使用情境下觀察，或去 Render 後台看 Metrics/Logs 確認保活 ping 有沒有持續出現。

## 已知限制 / 待辦

- 沒有官方支援的方式能讓 Render 免費方案完全不休眠，這個做法本質是「用掉大部分免費額度換取不休眠」的權衡，不是真正的官方保活機制
- 如果之後這個 Render workspace 又加了其他免費服務，需要重新評估額度是否會超過每月 750 小時上限
- `BASE_URL` 在本機開發環境預設是區網 IP（見 `docs/magic-link-login.md`），保活 job 在本機開發時會打區網 IP，失敗也只是印一行 log，不影響其他排程任務
