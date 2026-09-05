# 商品／購物車／訂單（含綠界 ECPay 金流準備）

## 功能概述

前端（mynotification App）要加購物車與綠界 ECPay 金流。金流串接前，商品的價格與庫存必須是**後端權威資料**，不能繼續讓前端直接打公開測試 API（DummyJSON）或信任前端當下顯示的金額／庫存數字。這次的改動把商品資料改由自家 DB 提供，並新增下單（建立訂單＋扣庫存）的端點；購物車本身刻意不落地到 DB，前端本機管理即可。

## 技術流程

```
App                          後端 (FastAPI)                         DB
 |-- GET /api/v1/products -------------->|
 |                          |-- SELECT ... WHERE is_active ORDER BY id -->|
 |<---- [{ id, title, price, ... }] ------|

 |-- POST /api/v1/orders --------------->|
 |    { items: [{product_id, qty}] }     |-- 合併重複 product_id 的數量
 |                                       |-- 逐項 UPDATE products
 |                                       |   SET stock = stock - qty
 |                                       |   WHERE id=... AND is_active
 |                                       |   AND stock >= qty
 |                                       |   RETURNING price            -->|
 |                                       |   （任一項沒有 RETURNING 資料列 = 庫存不足/已下架
 |                                       |     -> rollback 整張訂單、回 409）
 |                                       |-- INSERT Order + OrderItem（快照 price）-->|
 |                                       |-- COMMIT
 |<---- 201 { id, status:"pending", items: [...] } --|

 |-- GET /api/v1/orders/me -------------->|
 |                                       |-- SELECT Order + selectinload(items.product) -->|
 |<---- [{ id, status, total_amount, items:[...] }] --|
```

## 後端實作

### 異動檔案

| 檔案 | 異動內容 |
|---|---|
| `app/models.py` | 新增 `Product`（`external_id`／`title`／`description`／`category`／`price`／`thumbnail`／`images`／`stock`／`is_active`／`created_at`／`updated_at`）、`Order`（`user_id`／`status`／`total_amount`／`payment_provider`／`merchant_trade_no`／`payment_reference`／`created_at`／`paid_at`）、`OrderItem`（`order_id`／`product_id`／`quantity`／`unit_price`／`subtotal`）；`User` 新增 `orders` 關聯 |
| `app/schemas/product.py` | 新增 `ProductOut`（`id`／`title`／`description`／`category`／`price`／`thumbnail`／`images`） |
| `app/schemas/order.py` | 新增 `OrderItemCreate`／`OrderCreate`（request）、`OrderItemOut`／`OrderOut`（response） |
| `app/api/v1/endpoints/products.py` | 新增 `GET /products`、`GET /products/{id}` |
| `app/api/v1/endpoints/orders.py` | 新增 `POST /orders`、`GET /orders/me` |
| `app/api/v1/api.py` | 註冊 `prefix="/products"`、`prefix="/orders"` |
| `app/scripts/seed_products.py` | 新增可重複執行的 DummyJSON 商品匯入腳本 |
| `testAlembic/versions/31e9c0319aec_*.py` | 新增 `products`／`orders`／`order_items` 三張表 |

### API 規格

**GET `/api/v1/products`**（公開，不需要 JWT）——可加 `?category=beauty` 篩分類

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

只回傳 `is_active=true` 的商品，依 `id` 排序。

**GET `/api/v1/products/{id}`**（公開）——單一商品，找不到或已下架回 404。

**POST `/api/v1/orders`**（需登入，`Authorization: Bearer <token>`）

```json
// request
{ "items": [{ "product_id": 1, "quantity": 2 }] }

// response 201
{
  "id": 1,
  "status": "pending",
  "total_amount": 19.98,
  "payment_provider": "ecpay",
  "merchant_trade_no": "O260906015830a1b2",
  "created_at": "2026-09-06T01:58:30Z",
  "paid_at": null,
  "items": [{
    "product_id": 1, "title": "Essence Mascara Lash Princess",
    "quantity": 2, "unit_price": 9.99, "subtotal": 19.98
  }]
}
```

錯誤情況：
- `quantity <= 0` 或 `items` 為空陣列 → 422（Pydantic 驗證）
- 任一項商品不存在／已下架／庫存不足 → 409，**整張訂單失敗**，已扣的其他項目一併 rollback（all-or-nothing）
- 未帶 JWT → 401

**GET `/api/v1/orders/me`**（需登入）——目前使用者的訂單列表，格式同上，新到舊排序。

### 匯入腳本

`python -m app.scripts.seed_products` 打 `https://dummyjson.com/products?limit=0`，用 `external_id`（DummyJSON 原始 id）做 upsert（已存在就更新欄位、否則新增），可重複執行不會產生重複資料。純粹是開發測試用的展示資料，不是正式商品目錄。目前已執行過一次，匯入 194 筆。

## 關鍵設計決策

### 為什麼價格／庫存要以後端為權威資料

前端提出需求時就明確定調：金流串接前，不能信任前端傳來的金額或畫面上當下顯示的庫存數字（可能是快取的舊資料，或被竄改的 request）。所以 `POST /orders` 的 request body 只帶 `product_id` + `quantity`，金額由後端在下單當下重新查 `Product.price` 計算；`OrderItem.unit_price`/`subtotal` 存的是這個下單當下的**價格快照**，不是即時 join `Product.price`——避免之後改價連動修改到歷史訂單金額。

### 為什麼用 `Numeric` 不用 `Float`

金額欄位（`price`／`total_amount`／`unit_price`／`subtotal`）一律用 `Numeric(10, 2)`，避免 `Float` 二進位浮點數運算金額時的精度誤差（例如 `0.1 + 0.2 != 0.3` 這類問題）。

### 為什麼用「UPDATE ... WHERE stock >= 數量」而不是「先 SELECT 讀庫存、判斷夠不夠、再 UPDATE」

先讀後寫（read-then-write）在並發情況下有 race condition：兩個請求同時讀到「庫存還有 1 件」，都判斷通過，各自扣 1 件，庫存變成 -1（超賣）。改用單一 `UPDATE ... WHERE stock >= :qty RETURNING price` 的寫法，把「檢查庫存夠不夠」跟「扣庫存」合併成資料庫層級的單一原子操作，PostgreSQL 保證同一列的 `UPDATE` 彼此互斥，不會有兩個請求同時扣成功導致庫存為負的情況。這個 pattern 沿用專案裡 Magic Link 驗證（`app/api/v1/endpoints/login.py`）與 Coupon 核銷（`app/api/v1/endpoints/coupons.py`）已經在用的原子性 UPDATE 寫法。

### 為什麼訂單建立要 all-or-nothing

一次下單可能包含多個商品。如果訂單裡第 2 項商品庫存不足才失敗，但第 1 項已經扣了庫存，就會出現「賣出庫存卻沒有對應訂單」的資料不一致。目前的實作把所有商品的扣庫存都放在同一個 DB transaction 裡，任一項失敗就整個 `rollback()`，確保「訂單建立成功」與「庫存確實被扣」永遠同進退。

### 為什麼 `GET /orders/me` 用 `selectinload` 而不是讓關聯自然 lazy-load

這個專案的 DB session 是 `AsyncSession`（見 `CLAUDE.md` 的資料庫章節）。在非同步 session 底下，如果沒有預先用 `selectinload`／`joinedload` 明確指定要一起撈的關聯，事後才去存取 `order.items` 或 `item.product` 會觸發同步的 lazy-load，在 async driver 下會直接丟出 `MissingGreenlet` 例外。`orders.py` 用 `selectinload(Order.items).selectinload(OrderItem.product)` 一次把訂單、明細、商品都撈出來，除了避免這個例外，也避免 N+1 查詢問題。

### 為什麼購物車不落地到 DB

前端確認不需要跨裝置同步購物車，維持「前端本機管理購物車（例如 AsyncStorage），結帳當下才把 `[{product_id, quantity}]` 送到後端建立 `Order`」的設計，可以少一張 `Cart`/`CartItem` 表。如果之後需求變成要跨裝置同步，再評估加表。

### 為什麼 `merchant_trade_no` 這樣產生

綠界 ECPay 要求 `MerchantTradeNo` 是英數字、長度上限 20 碼、同一特店（Merchant）底下不能重複。目前用「時間戳（到秒，12 碼）+ 2 bytes 隨機碼（4 碼英數）+ 前綴 `O`」組出 17 碼，同一秒內撞號機率極低；真的撞號時會被資料庫的 `unique=True` 限制擋下、走 500 錯誤處理，還沒有做「撞號自動重試」的機制（見下方已知限制）。

## 已知限制／待辦

- **ECPay 串接尚未完成**：`POST /orders` 目前只會建立 `status="pending"` 的訂單並扣庫存，**還沒有實際呼叫 ECPay 的 Checkout API、也還沒有驗證付款完成的 callback**。需要商店的 `MerchantID`／`HashKey`／`HashIV`（要等實際申請到綠界的商店測試／正式環境金鑰後才能串）。串接時要額外注意 callback 簽章驗證（ECPay 的 `CheckMacValue`），避免有人偽造付款成功的請求把 `Order.status` 改成 `paid`。
- **訂單逾時未取消**：`status="pending"` 的訂單如果使用者一直沒去 ECPay 完成付款，目前沒有自動取消機制歸還庫存——之後可能需要一個排程任務，把超過一段時間仍是 `pending` 的訂單標記 `cancelled` 並把 `Product.stock` 加回去。
- **`merchant_trade_no` 撞號沒有自動重試**：機率極低但理論上可能發生，目前撞號會直接回 500，前端需要重新送出下單請求。
- **`Order.status` 目前沒有 `refunded`／退款流程**：等實際串 ECPay 之後，可能需要視綠界支援的退款 API 再擴充。
- 商品資料是 DummyJSON 原始的 194 筆（美妝、手錶、手機等雜項分類），不是正式商品目錄；之後要換成真正商品時，直接清空 `products` 表重新手動建資料或改匯入來源即可，不影響 API 形狀。

## 驗證方式

1. 本機啟動 API，確認 `app.main5:app` 匯入成功、`/api/v1/products`、`/api/v1/orders`、`/api/v1/orders/me` 路由都有掛載。
2. `curl GET /api/v1/products`（本機）確認公開可讀、欄位齊全。
3. 未帶 JWT 打 `GET /orders/me`、`POST /orders` 皆回 401，確認保護正確。
4. 對 Supabase 執行 `alembic upgrade head` 建表，執行 `python -m app.scripts.seed_products` 匯入 194 筆商品並用獨立查詢腳本確認筆數與樣本資料正確。
5. push 到 GitHub 後，等 Render Auto-Deploy 跑完，用 WebFetch 打線上 `https://fastapi-pj-2.onrender.com/api/v1/products` 確認回傳 200 且是新的商品資料格式（DummyJSON 商品，而非舊版行為）。
