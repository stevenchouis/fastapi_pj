# 會員等級（Member Tier）

2026-11-07 由 mynotification 提出（規格：mynotification repo 的 `plan-member-tier.md`），staff-scanner 顯示等級徽章。

## 規則

| | 一般會員 `regular` | 銀卡 `silver` | 金卡 `gold` |
|---|---|---|---|
| 年度消費門檻（台灣時間自然年度） | NT$0 | NT$10,000 | NT$30,000 |
| 點數回饋倍率 | 1 | 1.5 | 2 |
| 升等禮券（30 天效期） | — | $100 | $200 |
| 生日禮券（基礎 $100） | $100 | $200 | $300 |

- **等級 = max(去年全年消費換算的等級, 今年至今消費換算的等級)**，每次查詢即時計算，**不存 DB、沒有年底結算排程**。
- 等級效期：今年消費撐得起 → 明年 12/31；只靠去年消費撐著 → 今年 12/31；一般會員 `null`。
- **計入範圍**（`loyalty_service._spend_in_range`）跟賺點一樣：已付款網購訂單（`Order.paid_at` 落在年度內，含之後轉 `shipped` 的）＋已完成堂食（`status="completed"`，用 `created_at` 近似完成時間）＋會員快速結帳（`StoreCheckout.created_at`）。金額都是折抵後的實付金額 `total_amount`。
- 所有常數寫在 `app/services/loyalty_service.py`（`TIER_THRESHOLDS`／`EARN_MULTIPLIER`／`TIER_UPGRADE_COUPON_*`）與 `app/services/scheduler_service.py`（`BIRTHDAY_COUPON_*`），要調整直接改常數，前端 `constants/loyalty.ts` 也寫死同一份，改了要通知 mynotification。

## 賺點與升等：`loyalty_service.process_tier_earn`

三個賺點觸發點共用這支（取代原本直接呼叫 `calc_earned_points` + `earn_points`）：

| 觸發點 | 檔案 |
|---|---|
| ECPay callback 付款成功 | `orders.py` `ecpay_callback` |
| 堂食標記 `completed` | `dine_in_orders.py` `update_dine_in_order_status` |
| 會員快速結帳 | `store_checkouts.py` `create_store_checkout` |

流程：

1. `SELECT ... FOR UPDATE` 鎖住該使用者的 `User` 列，序列化同一人同時多筆消費，避免兩筆都讀到同一個「消費前」基準而重複發券。
2. `db.flush()` 後查今年累計（此時**已包含這筆**），減掉本筆金額得到「消費前」。
   - 坑：session 是 `autoflush=False`，原本三個呼叫端有的 flush 過、有的沒有，直接查會有的重複計入本筆、有的沒有。統一先 flush 再扣掉本筆就不會依呼叫端而異。
3. 倍率用**消費前**的等級（這筆讓你升等，下一筆才享新倍率）；`floor(實付 ÷ 100 × 倍率)`，除完乘完才取 floor。`reason` 會帶倍率，例如「消費回饋：堂食訂單 #12（銀卡 1.5 倍）」。
4. 消費後等級 > 消費前等級 → 建立升等禮券（`restaurant_id=None`，連鎖層級，同新會員／生日禮券）。一次跨兩級只發目標等級那張。
5. 不自己 commit；回傳 `{"earned_points", "upgraded_to", "coupon_id"}`，呼叫端 commit 後用 `BackgroundTasks` 推播。

**「每級每年最多發一次」沒有額外紀錄表**：已付款 Order／已完成 DineInOrder／StoreCheckout 都沒有取消路徑（`/orders/{id}/cancel` 只收 `pending`，堂食 cancel 只收 `pending`／`served`），年度累計只會單調遞增，跨越事件每級每年最多發生一次。**之後若新增「已付款訂單退款」或「已完成堂食取消」，這個前提就破了**，要補一張發券紀錄表（或改查 `Coupon.title` 去重），也要處理 `reverse_earn`。

## 升等推播（契約，不能隨意改）

```json
{
  "title": "恭喜升等金卡！",
  "body": "送您一張 $200 升等禮券，之後消費點數 2 倍回饋",
  "data": { "type": "tier_upgraded", "screen": "Coupons", "coupon_id": 456, "tier": "gold" }
}
```
`app_id="mynotification"`。文案與 payload 集中在 `tier_upgrade_push_text`／`tier_upgrade_push_data`。

## API

**`GET /api/v1/loyalty/me`**（只加欄位，向下相容）：

```json
{
  "balance": 1234,
  "tier": "silver",
  "year_spend": 12000,
  "next_tier": "gold",
  "amount_to_next_tier": 18000,
  "tier_expires_at": "2027-12-31",
  "earn_multiplier": 1.5
}
```
金卡時 `next_tier`／`amount_to_next_tier` 為 `null`；一般會員 `tier_expires_at` 為 `null`。`amount_to_next_tier` 是「下一級門檻 − 今年累計」（只靠去年撐著的銀卡，今年要從 0 累積到 30,000 才升金卡）。

**`POST /api/v1/store-checkouts/lookup`** 回應新增 `tier`（給 staff-scanner 顯示徽章）。

**訂單回應的 `points_earned`**（`OrderOut`／`DineInOrderOut`／`StoreCheckoutOut`）：改成加總 `LoyaltyTransaction` 裡該筆實際入帳的 `earn` 點數（`loyalty_service.earned_points_by_related`，列表一次查完），不再用 1 倍重算——有倍率後重算會跟明細對不上。欄位名不變。

## 生日禮券

`send_birthday_coupons_async_task`（每月 25 日）發券時即時算該使用者的等級，金額 = $100 × 1／2／3；推播文案帶實際金額。

## 驗證（2026-11-07）

用 `AsyncSessionLocal` 對正式 DB 跑腳本，全部在同一個交易內、最後 rollback（事後確認沒有殘留測試帳號）：

- 消費 3,000 → 一般、30 點、`amount_to_next_tier=7000`
- 再 8,000（跨銀卡）→ 仍 1 倍 80 點、發 $100 券（`restaurant_id` 為 null）
- 銀卡再 1,000 → 15 點、reason 含「銀卡 1.5 倍」、`earned_points_by_related` 回 15、效期明年底
- 再 25,000（銀→金）→ 1.5 倍 375 點、升金卡；金卡再 500 → 10 點、不再發券
- 新帳號一筆 35,000 → 直接金卡、只有 1 張券
- 只有去年 12,000 → 今年銀卡、效期今年底、`amount_to_next_tier=30000`；今年再 3,000 → 1.5 倍 45 點、不算升等

**2026-11-08 前端實機測試通過**：進度條、升等推播實際送到手機（點開是 $100 禮券）、銀卡 1.5 倍入帳。

**沒有驗證到的**：生日禮券排程實跑。
