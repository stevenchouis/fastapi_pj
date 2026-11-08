# 點數兌換優惠券（Points Exchange）

2026-11-08 由 mynotification 提出（規格：mynotification repo 的 `plan-points-exchange.md`，使用者 2026-11-07 定案）。staff-scanner 不用改：換到的券跟一般券一樣核銷。

## 規則

| option_id | 所需點數 | 優惠券 | 1 點等值 |
|---|---|---|---|
| `coupon_100` | 90 | $100 折價券 | NT$1.11 |
| `coupon_300` | 250 | $300 折價券 | NT$1.20 |
| `coupon_500` | 400 | $500 折價券 | NT$1.25 |

- 券效期 30 天，`restaurant_id=null`（連鎖層級），網購、堂食、門市結帳都能用，不限門市。
- **兌換後不能退回點數**，沒有取消端點。
- 每人每月（台灣時間自然月）最多 **3 張**，三種合計。
- 扣點順序跟結帳折抵相同：先扣最早到期的 earn 批次（FIFO）。
- 目錄是常數，寫在 `app/services/loyalty_service.py` 的 `EXCHANGE_OPTIONS`／`EXCHANGE_COUPON_VALID_DAYS`／`EXCHANGE_MONTHLY_LIMIT`。前端以 API 回傳為準、不寫死，所以改數字不用通知前端改程式；**但 `option_id`（key）是對外契約，不能改名**。之後要做後台管理再改成資料表。

## API

**`GET /api/v1/loyalty/exchange-options`**（需登入）

```json
{
  "options": [
    { "id": "coupon_100", "title": "$100 折價券", "points_cost": 90, "discount_amount": 100, "valid_days": 30 },
    { "id": "coupon_300", "title": "$300 折價券", "points_cost": 250, "discount_amount": 300, "valid_days": 30 },
    { "id": "coupon_500", "title": "$500 折價券", "points_cost": 400, "discount_amount": 500, "valid_days": 30 }
  ],
  "monthly_limit": 3,
  "used_this_month": 1
}
```

**`POST /api/v1/loyalty/exchange`**（需登入），body `{"option_id": "coupon_100"}`

| 情況 | 回應 |
|---|---|
| 成功 | `201` `{"coupon": <CouponOut，同 /coupons/me>, "balance": <扣完的餘額>}` |
| 不認得的 `option_id` | `404`（`detail` 是字串） |
| 本月已達上限 | `409` `{"error_code": "exchange_limit_reached", "message": ...}` |
| 點數不足 | `409` `{"error_code": "insufficient_points", "message": ...}`（同結帳折抵的錯誤碼） |

## 點數明細

- 新 type **`exchange`**（固定負向），`reason` 例如「兌換 $100 折價券」，`related_coupon_id` 指向換到的券。`GET /loyalty/transactions` 的回應有帶 `related_coupon_id`。
- **為什麼不沿用 `redeem`**：`redeem` 代表「訂單折抵」、掛在訂單上，訂單取消時會依 `redeem` 退點（`reverse_redeem`）。混用容易誤退，報表也分不開。
- 實作上共用 `redeem_points`（新增 `txn_type`／`related_coupon_id` 參數，預設行為不變），FIFO 扣點邏輯只有一份。
- `LoyaltyTransaction.related_coupon_id`：migration `72fa08909b6d`，nullable，外鍵 **`ON DELETE SET NULL`**。原因是刪除帳號流程（`DELETE /users/me`）會先刪未使用的券、再刪點數明細，外鍵不設 SET NULL 的話刪帳號會失敗。
- Coupon **沒有**加 `source` 欄位（前端用不到；要追溯可以從明細的 `related_coupon_id` 反查）。

## 併發與交易

`loyalty_service.exchange_points_for_coupon`：

1. `SELECT ... FOR UPDATE` 鎖住該使用者的 `User` 列：同一人連點的兩個請求會排隊，第二個進來時看得到第一個已扣的點數和已記的明細。
2. 數本月 `type="exchange"` 的明細筆數（不另開紀錄表），≥ 3 回 `"limit"`。
3. 建券並 flush 拿 id，再呼叫 `redeem_points(txn_type="exchange")`。餘額不足回 `"insufficient"`，由端點 rollback，券不會留下。
4. 不自己 commit，由端點 commit；commit 前先把 `coupon.id` 存成區域變數，commit 後重新查券和餘額（避免 MissingGreenlet）。

## 驗證（2026-11-08）

用 `httpx.ASGITransport` 打真實端點、連正式 DB；整個測試包在外層交易裡（session 用 `join_transaction_mode="create_savepoint"`，端點的 commit 只會變成 savepoint），最後 rollback，事後確認沒有殘留：

- 目錄格式、`used_this_month=0`；未知 `option_id` 回 404
- 換 $100：201、餘額 1050→960、券 $100／30 天／`restaurant_id` null；FIFO 先扣早到期那批 50、再從晚的扣 40；明細 `exchange` 90 點、`related_coupon_id` 正確、reason「兌換 $100 折價券」
- 再換 $500、$300：餘額 310、`used_this_month=3`；第 4 張回 409 `exchange_limit_reached`
- 點數 80 換 $100：409 `insufficient_points`，沒有留下券，餘額不變
- 刪掉換到的券後，明細的 `related_coupon_id` 自動變 null（外鍵 SET NULL 生效）

**沒有驗證到的**：真正的兩個並發請求同時打進來（鎖的行為是依 PostgreSQL `FOR UPDATE` 的語意推論，跟會員等級同一套做法）、前端實機。
