---
name: add-list-endpoint
description: 新增一個「前端可控清單類」公開端點（例如首頁輪播、熱門搜尋標籤這類由後台維護、無需權限模型的列表）。使用時機：使用者要求新增一個由運營／後台手動維護資料、前端純讀取顯示的公開 GET 清單端點時觸發，例如「新增一個活動標籤清單」「新增首頁 xxx 輪播」。
---

# 新增前端可控清單類端點

此專案已有兩個這類端點可參考：`GET /api/v1/search/suggestions`（熱門搜尋標籤）與
`GET /api/v1/promotions/home-banners`（首頁活動輪播）。兩者都是「後台手動維護資料表、
端點公開無需 JWT、前端只讀」的模式。新增同類端點時依循以下步驟。

## 步驟

1. **在 [app/models.py](../../../app/models.py) 新增 ORM model**
   - 至少包含 `id`、`is_active: Boolean`（預設 True，用於下架不刪資料）、
     `sort_order: Integer`（預設 0，數字越小越前面）、`created_at`（`server_default=func.now()`）。
   - 若清單項目有生效時間區間（像 Promotion 的活動檔期），加上
     `start_at` / `end_at`（`DateTime(timezone=True)`, `nullable=True`）。
   - 參考既有的 `SearchSuggestion` / `Promotion` class 寫法與繁體中文註解風格。

2. **在 [app/schemas/](../../../app/schemas/) 新增對應的 `*Out` schema 檔案**
   - 檔名對應資源名稱，例如 `app/schemas/xxx.py`。
   - 只寫前端實際需要的欄位——不要把 `is_active`、`sort_order`、`start_at`/`end_at`
     這些後台維護用的欄位塞進回傳 schema（`PromotionOut` 就只回傳
     `id/tag/title/subtitle/color/image_url`，不含排序或啟用狀態欄位）。
   - 加上 `class Config: from_attributes = True` 讓它能直接從 SQLAlchemy model 轉換。
   - **注意**：這兩個既有的 schema（`search.py`／`promotion.py`）並未在
     `app/schemas/__init__.py` 的 `__all__` 匯出清單中，是直接從
     `app.schemas.xxx` 匯入使用；新的 schema 可以照這個既有慣例（不強制加進 `__init__.py`）。

3. **在 `app/api/v1/endpoints/` 新增 endpoint 檔案**
   - 開頭加上 `# app/api/v1/endpoints/xxx.py` 註解（延續既有檔案慣例）。
   - 用 `AsyncSession = Depends(get_db)`（來自 `app.database_async`），2.0 風格 `select()`。
   - 查詢條件：`.where(Model.is_active.is_(True))`；若有生效區間，加上
     `.where(or_(Model.start_at.is_(None), Model.start_at <= now))` 這種「欄位為
     None 或落在區間內」的寫法（`now = datetime.now(UTC)`）。
   - `.order_by(Model.sort_order)`。
   - 端點本身不加任何驗證依賴（公開端點，無需 JWT）——除非使用者明確要求要權限保護。
   - `response_model=List[XxxOut]`。
   - 幫函式寫一段繁體中文 docstring，說明這是給前端哪個畫面用、篩選與排序邏輯。

4. **在 [app/api/v1/api.py](../../../app/api/v1/api.py) 註冊路由**
   - 加進 import list（依英文字母排序，跟現有 `coupons, items, login, notifications,
     promotions, search, users` 的排序方式一致）。
   - `api_router.include_router(xxx.router, prefix="/xxx", tags=["xxx"])`。

5. **產生 Alembic migration**（腳本目錄是 `testAlembic/`，不是預設的 `alembic/`）
   ```powershell
   alembic revision --autogenerate -m "新增 Xxx model"
   alembic upgrade head
   ```
   產生後**務必打開檢查**自動產生的 migration 檔案內容是否符合預期，再執行 upgrade。

## 完成後檢查清單

- [ ] Model 有 `is_active` 和 `sort_order`（除非使用者明確說不需要下架／排序機制）
- [ ] Out schema 只含前端需要的欄位，不外洩後台維護欄位
- [ ] Endpoint 沒有掛任何 JWT 依賴（除非使用者要求要保護）
- [ ] 路由已在 `api.py` 註冊，且 import 順序有維持字母排序
- [ ] Alembic migration 已產生並人工檢查過內容
