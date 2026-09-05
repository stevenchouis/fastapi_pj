# 收藏／願望清單

## 功能概述

前端（mynotification App）商店 Grid 卡片、商品詳情頁、「我的」分頁的收藏區段，都需要一份「使用者收藏了哪些商品」的清單。前端確認正式版要直接存 DB（不是本機儲存），這樣才能跨裝置同步、換手機也不會遺失收藏紀錄。

## 技術流程

```
App                          後端 (FastAPI)                         DB
 |-- POST /api/v1/favorites ------------->|
 |    { product_id: 1 }                   |-- 查 Product 是否存在且上架
 |                                        |-- 查是否已收藏過（有就直接回傳，idempotent）
 |                                        |-- INSERT Favorite -->|
 |<---- 204 No Content --------------------|

 |-- DELETE /api/v1/favorites/{id} ------->|
 |                                        |-- DELETE WHERE user_id AND product_id -->|
 |<---- 204 No Content --------------------|

 |-- GET /api/v1/favorites/me ------------>|
 |                                        |-- SELECT Product JOIN Favorite
 |                                        |   WHERE user_id ORDER BY favorited_at DESC -->|
 |<---- [{ id, title, price, ... }] -------|
```

## 後端實作

### 異動檔案

| 檔案 | 異動內容 |
|---|---|
| `app/models.py` | 新增 `Favorite`（`user_id`／`product_id`／`created_at`，`(user_id, product_id)` unique constraint）；`User` 新增 `favorites` 關聯、`Product` 新增 `favorited_by` 關聯 |
| `app/schemas/favorite.py` | 新增 `FavoriteCreate`（`product_id`） |
| `app/api/v1/endpoints/favorites.py` | 新增 `POST /favorites`、`DELETE /favorites/{product_id}`、`GET /favorites/me` |
| `app/api/v1/api.py` | 註冊 `prefix="/favorites"` |
| `testAlembic/versions/73c591d06cea_*.py` | 新增 `favorites` 表 |

### API 規格

**POST `/api/v1/favorites`**（需登入，`Authorization: Bearer <token>`）

```json
// request
{ "product_id": 1 }
// 成功回 204 No Content（空 body）
```

錯誤情況：商品不存在或已下架 → 404；未帶 JWT → 401。**已經收藏過同一商品也回 204，不會報錯**（見下方「為什麼設計成 idempotent」）。

**DELETE `/api/v1/favorites/{product_id}`**（需登入）——回 204。本來就沒收藏過也回 204（同樣是 idempotent，等同 REST 慣例：刪除一個不存在的資源不算失敗）。

**GET `/api/v1/favorites/me`**（需登入）——直接回傳完整商品資料，格式跟 `GET /api/v1/products`（見 `docs/products-and-orders.md`）完全一樣：

```json
[{
  "id": 1,
  "title": "Essence Mascara Lash Princess",
  "description": "...",
  "category": "beauty",
  "price": 9.99,
  "thumbnail": "https://cdn.dummyjson.com/...",
  "images": ["https://cdn.dummyjson.com/...", "..."]
}]
```

依收藏時間新到舊排序，只回傳 `is_active=true` 的商品（商品下架後會自動從收藏清單消失，但收藏紀錄本身不會被刪除——如果之後商品重新上架，收藏紀錄還在）。

## 關鍵設計決策

### 為什麼 `POST`／`DELETE` 都設計成 idempotent

前端明確要求：重複收藏同一個商品不要報錯，直接回成功，前端不用先查一次「這個商品是否已經收藏過」才決定要呼叫新增還是不呼叫。實作上：
1. 新增前先查一次是否已存在，存在就直接回傳（不重複 `INSERT`）。
2. 即使檢查後、真正 `INSERT` 前，剛好被同一個使用者「連續點兩下」造成競態（两個請求都通過了步驟 1 的檢查），也用 `try/except IntegrityError` 接住資料庫 unique constraint 擋下的重複寫入，一樣視為成功回傳，不讓使用者看到錯誤訊息。
3. `DELETE` 天生就是 idempotent：刪除一筆不存在的收藏紀錄，`DELETE ... WHERE` 條件比對不到任何列，執行結果一樣是「這個使用者現在沒有收藏這個商品」，跟先確認存在再刪除的結果相同，所以不需要額外判斷。

### 為什麼資料庫層要加 `UniqueConstraint(user_id, product_id)`

雖然應用層已經有「先查是否存在再新增」的邏輯，但那沒辦法完全避免競態（見上一點）。在資料庫層加上 unique constraint，才能真正保證「不管程式邏輯有沒有漏洞、不管有多少併發請求，同一個使用者對同一個商品最多只會有一筆收藏紀錄」，這是資料完整性的最後一道防線，不能只靠應用層邏輯保證。

### 為什麼 `GET /favorites/me` 直接回完整商品資料，而不是只回 `product_id` 陣列

前端明確要求比照 `GET /orders/me` 內嵌完整 `items` 資料的做法（見 `docs/products-and-orders.md`）：如果只回一串 `product_id`，前端拿到之後還要對每個 id 再打一次 `GET /products/{id}`（或自己在前端做一次商品清單比對），才能在 Grid／收藏清單畫面上顯示標題、價格、縮圖。直接在後端用一次 SQL join（`Product` JOIN `Favorite`）把完整商品資料撈出來回傳，前端可以直接渲染，少了 N 次額外的 API 呼叫。

## 已知限制／待辦

- 收藏清單目前沒有分頁——如果使用者收藏商品數量變多（例如幾百筆），`GET /favorites/me` 會一次全部回傳，之後如果真的變成效能問題，可以加 `limit`/`cursor` 分頁參數。
- 商品下架後收藏紀錄不會自動清除，只是查詢時被 `is_active` 條件濾掉；如果商品之後被真正刪除（目前沒有刪除商品的端點），`Favorite` 這筆紀錄會因為 `Product.favorited_by` 的 `cascade="all, delete-orphan"` 設定跟著透過 ORM 刪除（僅限用 ORM 刪除 `Product` 物件的情況，直接下 SQL `DELETE FROM products` 不會觸發這個 ORM 層 cascade）。

## 驗證方式

1. 本機啟動 API，確認 `POST /favorites`、`DELETE /favorites/{product_id}`、`GET /favorites/me` 三支路由都有掛載，且未帶 JWT 都回 401。
2. 對 Supabase 執行 `alembic upgrade head` 建立 `favorites` 表（含 unique constraint）。
3. push 到 GitHub 後，等 Render Auto-Deploy 跑完，用 WebFetch 打線上 `GET /api/v1/favorites/me` 確認回傳 401（代表路由已經部署上線，而不是 404 找不到路由）。
