# 首頁開屏活動彈窗（Popup Banner）

2026-11-09 由 mynotification 提出（規格：mynotification repo 的 `plan-popup-banner.md`，使用者已確認），後台管理在 staff-scanner。結構比照首頁大輪播（`HeroBanner`）。

## 資料

新表 `PopupBanner`（`popup_banners`，migration `b385b1204eb7`）：

| 欄位 | 說明 |
|---|---|
| `title` | 必填，給後台辨識和無障礙朗讀（彈窗本身是純圖片） |
| `image_url` | **必填**（跟 `HeroBanner` 不同，彈窗沒有底色可以退回）。圖片由 staff-scanner 上傳到 Supabase `promotions` bucket，檔名前綴 `popup/`，後端只存網址字串，沒有上傳端點 |
| `link_route` | 可 null（點了不導向）；有值必須在白名單內 |
| `sort_order` / `is_active` / `start_at` / `end_at` | 同 `HeroBanner` |
| `created_at` / `updated_at` | 自動 |

沒有任何表參照這張表，刪除是硬刪除。

## API

**`GET /api/v1/popup-banners`**（公開）：`[{id, title, image_url, link_route}]`，只回 `is_active=True` 且在 `start_at`/`end_at` 區間內（null 代表不限），依 `sort_order`（同值再依 `id`）排序。「一次顯示幾張、同一天不重複彈」由前端決定。

**管理端**（`verify_admin_or_staff` 雙軌：`X-Admin-Key` 或 `role="staff"` JWT，其他 401）：

- `GET /api/v1/popup-banners/admin`：全部，含未啟用、未生效、已過期。路由註冊在 `/{id}` 之前。
- `POST /api/v1/popup-banners`：201，回完整欄位。
- `PATCH /api/v1/popup-banners/{id}`：帶了的欄位絕對覆蓋，沒帶的不動；`link_route`／`start_at`／`end_at` 可帶 null 清除；`title`／`image_url` 帶 null 或空白、`sort_order`／`is_active` 帶 null 回 422（避免撞 DB NOT NULL 變 500）。找不到 404。
- `DELETE /api/v1/popup-banners/{id}`：204，找不到 404。暫時下架用 `PATCH {"is_active": false}`。

## link_route 白名單（跟大輪播共用）

清單移到 `app/schemas/link_route.py` 的 `BANNER_LINK_ROUTES`，`HeroBanner` 與 `PopupBanner` 的 Create/Update schema 都用同一個 validator，不在清單內回 **422**。2026-11-09 從 8 個加到 10 個：

`/coupons`、`/points`、`/favorites`、`/inbox`、`/member-code`、`/shop`、`/search`、`/settings`、**`/referral`**、**`/points-exchange`**

三方（後端／mynotification／staff-scanner）寫死同一份清單，要加新路由三邊都要改。

## 驗證（2026-11-09）

用 `httpx.ASGITransport` 打真實端點、連正式 DB（真的 JWT 走 `verify_admin_or_staff`），外層交易最後 rollback，事後確認 `popup_banners`、測試帳號、測試大輪播都沒有殘留。

- 沒帶授權、顧客 JWT 打管理端點都 401；店員 JWT 與 `X-Admin-Key` 都能建立／編輯
- 公開端點只回啟用且在生效區間內的項目、依 `sort_order` 排序、只有 4 個欄位；未啟用、未來生效、已過期都濾掉
- 白名單外 `link_route`、缺 `image_url`、空白 `image_url`、空 `title` 建立回 422
- PATCH 上架生效、清 `link_route`、改圖、沒帶欄位不動；`image_url`／`title`／`is_active` 帶 null 與白名單外路由回 422；不存在 404
- DELETE 真的刪除，再刪一次 404
- 大輪播：`/referral`、`/points-exchange` 建立與編輯都通過，白名單外仍 422

**沒有驗證到的**：前端實機。
