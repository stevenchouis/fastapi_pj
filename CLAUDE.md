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

**請求流程：** `main5.py` → 掛載 `app.api.v1.api.api_router` 於 `settings.API_V1_STR`（`/api/v1`）→ 各資源的路由分別放在 `app/api/v1/endpoints/`（`login`、`users`、`notifications`、`coupons`、`items`、`search`、`promotions`、`products`、`orders`、`favorites`、`menu_items`、`dine_in_orders`）。

**驗證機制：** 採用 JWT Bearer Token。`app/core/security.py` 負責產生／驗證 Token 及密碼雜湊（透過 passlib 的 argon2/bcrypt）。`app/api/deps.py` 提供 `get_current_user`，會解碼 Token 中的 `sub` 欄位作為 user ID 並查出對應的 `User`——在任何受保護的路由中將它作為 FastAPI 依賴項注入即可。除了帳密登入（`POST /api/v1/login/access-token`，OAuth2 password form）之外，還有三種第三方／無密碼登入方式，皆定義在 `app/api/v1/endpoints/login.py`，最終都是查找／建立 `User` 後簽發同一套 JWT：

- `POST /api/v1/login/google`——前端帶 Google `id_token`，後端用 `google-auth` 套件離線驗證簽章與 audience（`settings.GOOGLE_CLIENT_ID`），比對 `User.google_id`。
- `POST /api/v1/login/line`——前端用原生 SDK（`@xmartlabs/react-native-line`）登入後直接拿到 `id_token`，後端用 `httpx` 呼叫 LINE 的 `/oauth2/v2.1/verify` 驗證簽章與 audience（`settings.LINE_CHANNEL_ID`），比對 `User.line_id`。**注意：** LINE 預設只給 `sub`／暱稱，不含 Email（要拿 Email 需另外申請 LINE 官方權限），所以純 LINE 帳號的 `email` 允許為 `null`；`LINE_CHANNEL_ID`／`LINE_CHANNEL_SECRET` 從 `.env` 讀取，取得前預設為空字串。`GET /api/v1/login/line/redirect` 是舊版「瀏覽器 OAuth + 後端中繼落地頁」流程留下的端點（LINE Console 的 Callback URL 只接受 https，所以需要一個落地頁把 query params 轉跳到 App 的 `mynotification:///redirect` 自訂 scheme）；改用原生 SDK 後前端已不會再呼叫它，暫時保留、確認沒有其他地方依賴後可以整支移除。
- `POST /api/v1/login/magic-link/request` + `/verify`——Email 免密碼登入，透過 Resend 寄送一次性連結。

三種第三方登入都遵循同一個「先用第三方唯一 ID 找帳號，找不到才退而用 Email 找／合併既有帳號，都沒有才新建」的 pattern（`User.auth_provider` 標記來源：`password`／`google`／`line`／`magic_link`／`both`），新增其他第三方登入時可依循此模式。

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

**前端可控清單類端點（後台維護、無需複雜權限模型）：** 目前有兩個這類端點，資料表由營運／後台手動維護，端點本身皆為公開（無需 JWT）：

- `GET /api/v1/search/suggestions`（`app/api/v1/endpoints/search.py`）——搜尋頁「熱門搜尋標籤」，對應 `SearchSuggestion` model（`keyword`、`sort_order`、`is_active`）。
- `GET /api/v1/promotions/home-banners`（`app/api/v1/endpoints/promotions.py`）——首頁活動輪播，對應 `Promotion` model（`tag`、`title`、`subtitle`、`color`、`image_url`、`sort_order`、`is_active`、`start_at`/`end_at` 生效區間）。

新增同類端點時可依循這個模式：新增 model → `app/schemas/` 下建立對應 `*Out` schema（只回傳前端需要的欄位）→ endpoint 內用 `select` 篩 `is_active` 並依 `sort_order` 排序 → 在 `app/api/v1/api.py` 註冊路由。

**商品／購物車／訂單（2026-09 由前端 mynotification 提出，已上線）：** 前端加了購物車與綠界 ECPay 金流，商品資料改由自家 DB 提供，不再讓前端直接打 DummyJSON（金流串接前，價格／庫存必須是後端權威資料）。

- **`Product` model**（`app/models.py`）欄位：
  - `external_id`（Integer, unique, nullable）——DummyJSON 原始商品 id，供匯入腳本判斷「已存在就更新、否則新增」；手動建立的商品可為 `null`。
  - `title`／`description`／`category`／`thumbnail`／`images`（`images` 是 JSON 字串網址陣列）——對齊 DummyJSON 的回應欄位，減少前端改動。
  - `price`（`Numeric(10, 2)`，**不是 Float**，避免金額浮點數誤差）、`stock`（Integer）——**這兩個欄位是權威資料**，下單一律以資料庫當下的值為準，不採信前端傳入或畫面上顯示的數字。
  - `is_active`（Boolean，預設 `True`）——是否上架，下架不刪資料，慣例同 `SearchSuggestion`/`Promotion`。
  - `created_at`／`updated_at`（`server_default=func.now()`，`updated_at` 另加 `onupdate=func.now()`）。
- **`GET /api/v1/products`**（可加 `?category=xxx` 篩分類）、**`GET /api/v1/products/{id}`**（`app/api/v1/endpoints/products.py`）——公開端點（不需 JWT），只回傳 `is_active` 商品，回應 schema 是 `ProductOut`（`app/schemas/product.py`：`id`／`title`／`description`／`category`／`price`／`thumbnail`／`images`）。
- **`Order` model**：`user_id`（FK）、`status`（`pending`/`paid`/`failed`/`cancelled`，目前只會走到 `pending`）、`total_amount`（後端下單當下重新計算，不是前端傳來的金額）、`payment_provider`（固定 `"ecpay"`，保留欄位方便之後加第二家金流）、`merchant_trade_no`（我方系統產生、送給 ECPay 的訂單編號，英數字、長度上限 20 碼、`unique=True`）、`payment_reference`（ECPay 回調的 `TradeNo`，付款成功前為 `None`）、`paid_at`。
- **`OrderItem` model**：`order_id`／`product_id`（FK）、`quantity`、`unit_price`／`subtotal`——**下單當下的價格快照**，不是即時 join `Product.price`，避免之後改價影響歷史訂單金額。
- **`POST /api/v1/orders`**（需登入，`app/api/v1/endpoints/orders.py`）：request body `{"items": [{"product_id": int, "quantity": int}]}`。同一商品出現多次會先合併數量；用「`UPDATE products SET stock = stock - :qty WHERE id = :id AND is_active AND stock >= :qty RETURNING price`」原子性扣庫存（比照 Magic Link／Coupon 核銷已在用的寫法），**任一項商品庫存不足或已下架，整張訂單失敗、已扣的其他項目一併 rollback**（all-or-nothing，不會出現部分商品扣了庫存卻沒建立訂單的情況）；庫存不足回 409。回應 schema `OrderOut`（`app/schemas/order.py`）內嵌完整 `items`（含 `title`／`quantity`／`unit_price`／`subtotal`），用 SQLAlchemy `selectinload` 一次把 `Order.items`／`OrderItem.product` 都撈出來，避免 N+1 查詢與 async session 下存取未載入關聯會噴 `MissingGreenlet` 的問題。**另一個踩過的 `MissingGreenlet` 坑：** `AsyncSessionLocal` 沒有設定 `expire_on_commit=False`，`await db.commit()` 後 session 預設會把物件所有屬性標記為過期，這時如果直接存取剛 commit 完的物件的 `.id`（拿來重新查詢完整關聯用）會觸發「同步屬性存取觸發非同步重新查詢」而噴 `MissingGreenlet`（2026-09 在堂食點餐開發時發現，`orders.py` 原本也有這個 bug，已一併修正）。**慣例：** 需要在 commit 後用剛建立物件的 id 重新查詢時，務必在 `await db.commit()` 之前先 `await db.flush()` 並把 `.id` 存成區域變數，commit 後只用該區域變數，不要再碰物件屬性。
- **`GET /api/v1/orders/me`**（需登入）——取得目前使用者的訂單列表（新到舊），格式同 `OrderOut`。
- **`app/scripts/seed_products.py`**——可重複執行的 DummyJSON 商品匯入腳本（`python -m app.scripts.seed_products`），用 `external_id` 做 upsert（已存在就更新欄位、否則新增），純粹是開發測試用的展示資料，不是正式商品目錄。目前已匯入 194 筆。

**尚未完成、下一步要做的：** `POST /api/v1/orders` 目前只會建立 `status="pending"` 的訂單並扣庫存，**還沒有實際呼叫 ECPay** 的 Checkout API／驗證付款完成的 callback（需要商店的 `MerchantID`／`HashKey`／`HashIV`，要等申請到綠界的商店測試/正式環境金鑰後才能串；串接時要額外注意 callback 簽章驗證，避免偽造付款成功請求）。購物車本身刻意不落地到 DB（前端本機管理，結帳當下才把 `[{product_id, quantity}]` 送來建立 `Order`），沒有跨裝置同步需求；如果之後需求變了要加 `Cart`/`CartItem` 表再評估。

**收藏／願望清單（2026-09 由前端 mynotification 提出，已上線）：** 涵蓋商店 Grid 卡片、商品詳情頁、「我的」分頁的收藏區段，前端確認要直接存 DB（不是本機儲存）。

- **`Favorite` model**（`app/models.py`）：`user_id`／`product_id`（皆為 FK，皆有 index）、`created_at`。`__table_args__` 設定 `UniqueConstraint("user_id", "product_id")`，在資料庫層面防止同一使用者對同一商品重複收藏。
- **`POST /api/v1/favorites`**（需登入，`app/api/v1/endpoints/favorites.py`）：body `{"product_id": int}`。**設計成 idempotent**——已收藏過同一商品直接回 204，不報錯，前端不用另外判斷「是否已收藏」；先查商品是否存在且上架（不存在回 404），再查是否已收藏過（已收藏直接回傳），最後才 `INSERT`，並用 `try/except IntegrityError` 接住「同時點兩下」造成的 unique constraint 競態，一樣視為成功。
- **`DELETE /api/v1/favorites/{product_id}`**（需登入）——用 `DELETE ... WHERE user_id AND product_id`，本來就沒收藏過也回 204，同樣是 idempotent。
- **`GET /api/v1/favorites/me`**（需登入）——**直接回傳完整商品資料**（`join Favorite -> Product`，格式跟 `GET /api/v1/products` 完全一樣的 `ProductOut`），不是只回 `product_id` 陣列，比照 `GET /api/v1/orders/me` 內嵌完整資料的做法，前端渲染收藏清單/Grid 不用再逐一查詢商品明細。只回傳 `is_active` 的商品，依收藏時間新到舊排序。

**堂食點餐（2026-09 由前端 mynotification 提出，已上線第一版）：** 顧客到店用手機自助點餐（選桌號→瀏覽菜單→加入清單→送出），跟網購商店／購物車是分開的兩個流程，刻意不共用 `Product`/`Order`——理由是現有 `Order` 的欄位（`payment_provider`/`merchant_trade_no`/`payment_reference`/`paid_at`）是圍繞 ECPay 線上金流設計的，堂食不一定馬上有金流動作，硬塞會出現一堆語意不明的 nullable 欄位；`status` 語意也不同（堂食未來可能要有「備餐中／已出餐」這種現場流程狀態，跟網購的付款狀態不是同一回事）。

- **`MenuItem` model**（`app/models.py`）：`name`／`description`／`category`／`price`（`Numeric(10,2)`，同 `Product` 避免浮點誤差）／`image_url`／`is_available`（Boolean，預設 `True`）。**跟 `Product` 的關鍵差異：沒有 `stock`**——內用點餐沒有精準防超賣的需求，賣完由店員手動關閉 `is_available` 即可，不需要原子扣庫存。
- **`GET /api/v1/menu-items`**（可加 `?category=xxx`）、**`GET /api/v1/menu-items/{id}`**（`app/api/v1/endpoints/menu_items.py`）——公開端點，只回傳 `is_available` 的品項，回應 schema `MenuItemOut`（`app/schemas/menu_item.py`）。
- **`DineInOrder` model**：`user_id`（FK，需登入下單）、`table_number`（String——**前端是自由文字輸入框**，例如 `"A3"`，後端不做格式驗證或提供桌號清單查詢）、`status`（預設 `pending`，跟 `Order.status` 是各自獨立的欄位／語意）、`total_amount`（後端重新計算，不採信前端金額）。
- **`DineInOrderItem` model**：`order_id`／`menu_item_id`（FK）、`quantity`、`unit_price`／`subtotal`——下單當下的價格快照，做法比照 `OrderItem`。
- **`POST /api/v1/dine-in-orders`**（需登入，`app/api/v1/endpoints/dine_in_orders.py`）：request body `{"table_number": str, "items": [{"menu_item_id": int, "quantity": int}]}`。同一品項出現多次會先合併數量；價格一律以資料庫當下的值為準。沒有庫存扣減，只檢查品項存在且 `is_available`，缺一項就整單回 409（維持跟 `/orders` 一致的錯誤語意，但這裡沒有「已扣一部分要 rollback」的問題，因為本來就不扣庫存）。回應 schema `DineInOrderOut`（`app/schemas/dine_in_order.py`），用 `selectinload` 一次把 `DineInOrder.items`／`DineInOrderItem.menu_item` 都撈出來。
- **`GET /api/v1/dine-in-orders/me`**（需登入）——取得目前使用者的堂食點餐紀錄（新到舊），格式同 `DineInOrderOut`。
- **`app/scripts/seed_menu_items.py`**——可重複執行的菜單測試資料腳本（`python -m app.scripts.seed_menu_items`），用 `name` 做 upsert，純粹開發測試用，不是正式菜單。

**桌位管理（2026-09 由 staff session 提出，已上線）：** 店員端 App（staff-scanner）原本把桌位清單存在裝置本機 AsyncStorage，導致不同店員裝置看到的桌位清單不同步，改成存 DB 讓所有店員裝置共用同一份。

- **`Table` model**（`app/models.py`）：`code`（String，`unique=True`——桌號字串如 `"A3"`，store-scanner 端拿這個組 QR Code deep link）、`created_at`。**跟 `DineInOrder.table_number` 沒有 FK 關聯**——顧客端桌號是自由文字輸入不查表（見上方堂食點餐一節），`Table` 純粹是店員管理清單用，兩者故意脫鉤。
- **`GET /api/v1/tables`**／**`POST /api/v1/tables`**（body `{"code": str}`，重複回 409）／**`DELETE /api/v1/tables/{id}`**（找不到回 404），皆定義在 `app/api/v1/endpoints/tables.py`，皆需 `role="staff"`（見下方 role 機制一節）。
- 目前沒有多門市／租戶概念（`User`／`Product`／`MenuItem` 也都沒有），所以桌位清單是全域共用一份，刻意不加 `store_id` 卡位——真的要做多門市會是一次橫跨這幾張表的架構調整，不是現在能局部預留的。

**角色機制／`User.role`（2026-09，已上線）：** 店員端 App（`staff` session 負責的 staff-scanner）跟顧客走同一套帳密登入，之前有好幾個功能都卡在「無法區分顧客／店員」這件事上，這次一次補齊：

- **`User.role`**（`app/models.py`）：`String`，`server_default="customer"`，值只有 `"customer"`／`"staff"`。**沒有自助升級端點**——要開通店員帳號得直接去 DB 手動把某個既有帳號的 `role` 改成 `"staff"`（比照 `SearchSuggestion`/`Promotion` 後台手動維護的慣例）。`GET /api/v1/users/me`（`app/schemas/user.py` 的 `User` schema）會回傳這個欄位，App 登入後呼叫一次就知道自己是不是店員。
- **`app/api/deps.py` 新增兩個依賴**：
  - `get_current_staff_user`——包一層 `get_current_user`，再檢查 `role == "staff"`，不是就 403。給「只有店員能呼叫」的端點用。
  - `verify_admin_or_staff`——雙軌驗證，目前只有 `coupons/admin/issue` 在用：`X-Admin-Key` header **或** `role="staff"` 的 JWT，兩種認證方式擇一通過即可（用 `optional_oauth2 = OAuth2PasswordBearer(..., auto_error=False)` 讓沒帶 Authorization header 時不會直接被拒絕，才有機會改走 `X-Admin-Key` 那條路）。
- **這次一起收緊／串接的 4 個地方**：
  1. `POST /api/v1/dine-in-orders` 建立訂單後，透過 `send_role_push_notifications(db_factory, "staff", ...)`（`app/services/push_service.py`，新函式，跟既有的 `send_user_push_notifications` 共用底層的 `_publish_to_tokens` 發送迴圈）推播給所有 `role="staff"` 的使用者，每人各留一筆 `NotificationLog`。
  2. `POST /api/v1/coupons/redeem`（`app/api/v1/endpoints/coupons.py`）——依賴改成 `get_current_staff_user`，不再是任何登入帳號都能核銷任何人的優惠券；核銷碼本身仍然不檢查 `Coupon.user_id`（單次使用＋10 分鐘效期的憑證，任何店員核銷任何顧客的券是合理情境，這點不變）。
  3. `app/api/v1/endpoints/tables.py` 三支端點——依賴從 `get_current_user` 改成 `get_current_staff_user`。
  4. `POST /api/v1/coupons/admin/issue`——`dependencies` 從單純 `verify_admin_key` 改成 `verify_admin_or_staff`，`X-Admin-Key`（老闆／營運用 Postman 手動發券）跟 `role="staff"` JWT（店員 App 登入後用自己帳號發券）兩條路都保留，staff session 那邊確認需要雙軌並存，不能只留其中一種。

**Model 結構補充：** `User` 對 `Order`、`Favorite`、`DineInOrder` 皆為一對多（cascade 同其他子關聯，使用者刪除時一併刪除）；`Product` 對 `OrderItem`、`Favorite`（`favorited_by`）為一對多；`MenuItem` 對 `DineInOrderItem` 為一對多。

## 專案慣例

- 既有程式碼中的註解與 docstring 大量使用繁體中文——修改這些檔案時請延續此慣例。
- 路由程式碼一律使用 `AsyncSession` 搭配 `sqlalchemy` 的 `select`／`update`／`delete`（2.0 風格的查詢寫法），而非舊式的 `Query` API。
- Endpoint 程式碼通常會用 `try/except` 包住 commit 操作，失敗時 rollback，並印出／記錄除錯資訊——修改某個檔案時，請延續該檔案既有的錯誤處理風格，避免在同一模組內混用不同寫法。
- **溝通語言：** 不論是回覆使用者，還是與前端（其他 Claude session，例如 mynotification 專案的 front-end session）跨 session 溝通，一律使用中文。
