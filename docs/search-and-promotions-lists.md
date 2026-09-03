# 前端可控清單類端點：熱門搜尋標籤 / 首頁活動輪播

## 功能概述

兩支給前端（mynotification App）用的公開唯讀端點，資料表由營運／後台手動維護，不需要複雜的權限模型：

- **熱門搜尋標籤**：搜尋頁在使用者還沒輸入關鍵字時顯示，點一下直接帶入該關鍵字查詢
- **首頁活動輪播**：首頁的小型行銷 banner 輪播卡片

兩者結構高度相似（都是「篩 `is_active` + 依 `sort_order` 排序」的唯讀清單），一起開發、一起記錄。

## 技術流程

```
App                        後端 (FastAPI)                  DB
 |-- GET /api/v1/search/suggestions ------------->|
 |                                    |-- SELECT ... WHERE is_active ORDER BY sort_order -->|
 |<---- [{ keyword }, ...] ------------------------|

 |-- GET /api/v1/promotions/home-banners ---------->|
 |                                    |-- SELECT ... WHERE is_active AND 落在 start_at/end_at 區間 ORDER BY sort_order -->|
 |<---- [{ id, tag, title, subtitle, color, image_url }, ...] --|
```

## 後端實作

### 異動檔案

| 檔案 | 異動內容 |
|---|---|
| `app/models.py` | 新增 `SearchSuggestion`（`keyword`、`sort_order`、`is_active`、`created_at`）、`Promotion`（`tag`、`title`、`subtitle`、`color`、`image_url`、`sort_order`、`is_active`、`start_at`、`end_at`、`created_at`） |
| `app/schemas/search.py` | 新增 `SearchSuggestionOut`（只回傳 `keyword`） |
| `app/schemas/promotion.py` | 新增 `PromotionOut`（回傳 `id`、`tag`、`title`、`subtitle`、`color`、`image_url`；不回傳 `sort_order`/`is_active`/時間欄位） |
| `app/api/v1/endpoints/search.py` | 新增 `GET /search/suggestions` |
| `app/api/v1/endpoints/promotions.py` | 新增 `GET /promotions/home-banners` |
| `app/api/v1/api.py` | 註冊上述兩個路由（`prefix="/search"`、`prefix="/promotions"`） |
| `testAlembic/versions/974ed47e55f6_*.py` | 新增 `search_suggestions` 表 |
| `testAlembic/versions/e817d6ef4976_*.py` | 新增 `promotions` 表 |

### API 規格

**GET `/api/v1/search/suggestions`**（公開，不需要 JWT）

Response:
```json
[{ "keyword": "沐浴乳" }, { "keyword": "收納盒" }]
```

只回傳 `is_active=true`，依 `sort_order` 由小到大排序。

**GET `/api/v1/promotions/home-banners`**（公開，不需要 JWT）

Response:
```json
[{
  "id": 1,
  "tag": "限時",
  "title": "9 月會員日",
  "subtitle": "單筆消費滿 $999 折 $100",
  "color": "#F3EDE4",
  "image_url": "https://..."
}]
```

只回傳 `is_active=true`，且若有設定 `start_at`/`end_at` 也要落在生效區間內（皆為 `null` 代表沒有時間限制），依 `sort_order` 由小到大排序。

### 新增同類端點的建置模式

之後若要加其他「前端可控清單類端點」，可依循同一套模式：新增 model → `app/schemas/` 下建對應 `*Out` schema（只回傳前端需要的欄位）→ endpoint 內用 `select` 篩 `is_active` 並依 `sort_order` 排序 → 在 `app/api/v1/api.py` 註冊路由。這個模式也記錄在 `CLAUDE.md` 的「前端可控清單類端點」段落。

## 目前資料

兩張表都已在 Supabase 上建好並塞入前端提供的種子資料：
- `search_suggestions`：6 筆（沐浴乳、收納盒、文具、廚房用品、香氛、寢具）
- `promotions`：4 筆（9 月會員日、秋冬選物特輯、集點兌換、快閃線上市集）

## 為什麼不做成有權限控管的後台 CRUD API

前端提出需求時就明確定調「這應該是營運／後台手動維護的清單，先不需要複雜的權限模型」，所以目前資料是由後端（我）直接下 SQL/Python script 寫入，沒有另外開對外的新增/修改/刪除 API。如果之後真的需要讓非技術人員自己維護，才需要另外設計一套帶權限的後台管理介面。

## 已知限制 / 待辦

- 沒有管理端點可以新增/修改/停用資料，目前完全靠後端手動操作資料庫
- 沒有快取，每次請求都會直接查資料庫（這兩張表資料量小、查詢頻率低，目前不是問題）
