from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

# python .（點）代表層級：在 Python 匯入系統中，
# 一個點 . 就已經完整代表了「當前路徑下的套件（Current Package）」
# 下例表示由models.py目前目錄下的database.py模組import Base Class
from .database_async import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    # 新增生日欄位，允許為空（以免舊資料噴錯）
    birthday = Column(Date, nullable=True)
    hashed_password = Column(String, nullable=True)  # 純 Google 帳號沒有密碼，允許為空
    is_active = Column(Boolean, default=True)
    avatar_url = Column(String, nullable=True)  # 新增這一行
    # Google 登入用：Google 帳號的唯一識別碼（sub）
    google_id = Column(String, unique=True, index=True, nullable=True)
    # LINE 登入用：LINE 帳號的唯一識別碼（sub）。LINE 預設不提供 Email，
    # 所以純 LINE 帳號的 email 欄位允許為 null，改用 line_id 當識別依據
    line_id = Column(String, unique=True, index=True, nullable=True)
    # 標記帳號註冊來源：'password' / 'google' / 'line' / 'both'
    auth_provider = Column(String, nullable=False, server_default="password")
    # 帳號角色：'customer' / 'staff'。目前沒有自助升級端點，
    # 要開通店員帳號得直接去 DB 手動改這個欄位（比照 SearchSuggestion/Promotion
    # 後台手動維護的慣例）
    role = Column(String, nullable=False, server_default="customer")
    # 紅利點數餘額——權威資料，異動一律走原子性 UPDATE（比照 Product.stock），
    # 不可為負；效期／明細記錄在 LoyaltyTransaction
    loyalty_balance = Column(Integer, nullable=False, server_default="0")
    # 店員帳號歸屬的門市（僅 role="staff" 有意義）。一個店員只能屬於一間門市
    # （2026-09 跟 mynotification/staff-scanner 三方確認過的業務規則，不是關聯表）；
    # nullable 是為了向下相容既有帳號跟顧客帳號（顧客沒有門市歸屬）
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=True)
    # 2026-09 會員條碼/QR Code 門市收銀：限時會員辨識碼，做法比照 Coupon 的
    # redeem_code_hash/redeem_code_expires_at（只存 hash，不存明文；沒產生過、
    # 已被使用、或已重新產生過就是 None）。使用者手動按按鈕產生，不是自動輪替。
    member_code_hash = Column(String, nullable=True, index=True)
    member_code_expires_at = Column(DateTime(timezone=True), nullable=True)

    # 建立與 PushToken 的關聯
    push_tokens = relationship(
        "PushToken", back_populates="user", cascade="all, delete-orphan"
    )

    # 2. 補上這行：建立與 NotificationLog 的關聯 (解決 InvalidRequestError)
    notifications = relationship(
        "NotificationLog", back_populates="user", cascade="all, delete-orphan"
    )
    # 新增這行來對接 Coupon
    coupons = relationship(
        "Coupon", back_populates="user", cascade="all, delete-orphan"
    )
    # 購物車金流：對接訂單（一個使用者可以有多筆訂單）
    orders = relationship(
        "Order", back_populates="user", cascade="all, delete-orphan"
    )
    # 收藏／願望清單
    favorites = relationship(
        "Favorite", back_populates="user", cascade="all, delete-orphan"
    )
    # 到店自助點餐（跟網購 orders 是分開的兩個流程）
    dine_in_orders = relationship(
        "DineInOrder", back_populates="user", cascade="all, delete-orphan"
    )
    # 紅利點數明細帳本（賺取/折抵/過期紀錄）
    loyalty_transactions = relationship(
        "LoyaltyTransaction", back_populates="user", cascade="all, delete-orphan"
    )


class PushToken(Base):
    __tablename__ = "push_tokens"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    token = Column(
        String, unique=True, index=True, nullable=False
    )  # 儲存 ExpoPushToken[xxx...]
    device_name = Column(String, nullable=True)  # 可選：辨識裝置類型 (如 "iPhone 15")
    # 這個 token 是哪個 App 註冊的："mynotification" / "staff-scanner"。
    # nullable 是為了向下相容還沒更新的舊版 App（沒帶這個欄位就存 None）；
    # 發送推播時目前還沒有依此欄位篩選（2026-09 三方分階段上線中，見 CLAUDE.md
    # 「推播通知」一節的 rollout 計畫，等兩邊 App 都改送這個欄位後才會開始篩選）
    app_id = Column(String, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="push_tokens")


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    title = Column(String)
    body = Column(String)
    data = Column(JSON, nullable=True)  # 儲存跳轉參數，例如 {"screen": "Feeder"}
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="notifications")


class Coupon(Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    title = Column(String)  # 例如: "5月壽星禮"
    discount_amount = Column(Float)  # 折扣金額，例如: 100.0
    is_used = Column(Boolean, default=False)
    used_at = Column(DateTime(timezone=True), nullable=True)  # 使用時間
    expired_at = Column(DateTime(timezone=True))  # 到期時間
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # 核銷碼：只存 hash，不存明文；沒有產生過或已核銷/已重新產生過就是 None
    redeem_code_hash = Column(String, nullable=True, index=True)
    redeem_code_expires_at = Column(DateTime(timezone=True), nullable=True)
    # 2026-09 多門市支援：純記錄用途，標記這張券是哪個門市發的（如果有的話）。
    # NULL＝連鎖層級（新會員/生日禮券都是這種）。**不影響核銷**——任何門市的
    # 店員都能核銷任何優惠券，跟這欄位加入前的行為完全一致，這裡刻意不加限制。
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=True)

    # 建立關聯
    user = relationship("User", back_populates="coupons")


class SearchSuggestion(Base):
    __tablename__ = "search_suggestions"

    id = Column(Integer, primary_key=True, index=True)
    keyword = Column(String, nullable=False)  # 熱門搜尋標籤文字
    sort_order = Column(Integer, nullable=False, default=0)  # 顯示順序，數字越小越前面
    is_active = Column(Boolean, nullable=False, default=True)  # 是否啟用（下架不刪資料）
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Promotion(Base):
    __tablename__ = "promotions"

    id = Column(Integer, primary_key=True, index=True)
    tag = Column(String, nullable=False)  # 卡片短標籤，例如「限時」「新品」
    title = Column(String, nullable=False)
    subtitle = Column(String, nullable=False)
    color = Column(String, nullable=False)  # 卡片底色 hex
    image_url = Column(String, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)  # 顯示順序，數字越小越前面
    is_active = Column(Boolean, nullable=False, default=True)  # 是否啟用（下架不刪資料）
    start_at = Column(DateTime(timezone=True), nullable=True)  # 生效起始時間，可為空
    end_at = Column(DateTime(timezone=True), nullable=True)  # 生效結束時間，可為空
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    # DummyJSON 原始商品 id，供匯入腳本判斷「已存在就更新、否則新增」；
    # 手動建立（非 DummyJSON 匯入）的商品這欄位可以是 None
    external_id = Column(Integer, unique=True, index=True, nullable=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=False)
    category = Column(String, nullable=False)
    # 金額一律用 Numeric，避免 Float 的浮點數誤差
    price = Column(Numeric(10, 2), nullable=False)
    thumbnail = Column(String, nullable=False)
    # 對齊 DummyJSON 的 images 欄位：字串網址陣列
    images = Column(JSON, nullable=False, default=list)
    # 庫存以這裡為權威來源，下單時原子性扣減，不信任前端當下顯示的數字
    stock = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)  # 是否上架（下架不刪資料）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    order_items = relationship("OrderItem", back_populates="product")
    favorited_by = relationship(
        "Favorite", back_populates="product", cascade="all, delete-orphan"
    )


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    # pending / paid / failed / cancelled
    status = Column(String, nullable=False, default="pending")
    # 下單當下由後端重新計算，不採信前端傳入的金額
    total_amount = Column(Numeric(10, 2), nullable=False)
    # 目前固定綠界 ECPay，先保留欄位方便之後串第二家金流
    payment_provider = Column(String, nullable=False, default="ecpay")
    # 我方系統產生、送給金流的訂單編號（ECPay 的 MerchantTradeNo，長度限制 20 碼英數字）
    merchant_trade_no = Column(String, unique=True, index=True, nullable=False)
    # 金流那邊的交易編號（ECPay 回調的 TradeNo），付款成功前為 None
    payment_reference = Column(String, nullable=True)
    # 這筆訂單折抵用掉的點數／折抵金額，0 代表沒有使用點數；
    # total_amount 已經是扣除折抵後、實際要付款的金額
    points_used = Column(Integer, nullable=False, server_default="0")
    points_discount = Column(Numeric(10, 2), nullable=False, server_default="0")
    # 2026-09 優惠券線上折抵：跟核銷碼（到店給店員掃）是不同通路，直接在下單當下
    # 驗證這張券屬於此使用者、未使用、未過期後原子性標記為已使用，不產生核銷碼。
    # coupon_discount 是這筆訂單實際折抵的金額（券面額 clamp 到不超過商品小計），
    # 0／NULL 代表沒有使用優惠券。折抵順序：先套用券折扣、再用「券後金額」計算
    # 點數折抵上限，避免兩者疊加算出負的 total_amount（詳見 create_order）
    coupon_id = Column(Integer, ForeignKey("coupons.id"), nullable=True)
    coupon_discount = Column(Numeric(10, 2), nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    paid_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="orders")
    items = relationship(
        "OrderItem", back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), index=True, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    # 下單當下的價格快照，不是即時 join Product.price——避免之後改價影響歷史訂單金額
    unit_price = Column(Numeric(10, 2), nullable=False)
    subtotal = Column(Numeric(10, 2), nullable=False)

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "product_id", name="uq_favorites_user_product"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="favorites")
    product = relationship("Product", back_populates="favorited_by")


class MenuItem(Base):
    __tablename__ = "menu_items"

    id = Column(Integer, primary_key=True, index=True)
    # 門市歸屬——菜單是每間門市各自獨立（業務確認過，不是連鎖共用同一份）；
    # nullable 原因同 Table.restaurant_id
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), index=True, nullable=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=False)
    category = Column(String, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    image_url = Column(String, nullable=False)
    # 內用點餐不需要像 Product 那樣原子扣庫存，賣完由店員手動關閉即可
    is_available = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    dine_in_order_items = relationship("DineInOrderItem", back_populates="menu_item")


class DineInOrder(Base):
    __tablename__ = "dine_in_orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    # 舊版「自由文字輸入桌號」流程留下的欄位，繼續保留向下相容（前端還沒全面
    # 改成「選桌號清單」之前，這欄位可能是唯一的桌號來源）；有 table_id 時，
    # 這裡會存那張桌子當下的 code，兩者不會互相矛盾
    table_number = Column(String, nullable=False)
    # 2026-09 多門市支援新增：顧客從清單選桌號時會有這兩個欄位；nullable 是因為
    # 舊版前端可能還是只送 table_number（見上）。restaurant_id 是從 table_id
    # 反查出來的門市，另外存一份是為了讓店員接單列表能直接依門市篩選，不用每次
    # 都 join Table 表
    table_id = Column(Integer, ForeignKey("tables.id"), nullable=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), index=True, nullable=True)
    # pending / preparing / served / cancelled——現場出餐流程狀態，
    # 跟網購 Order.status 的付款狀態語意不同，故分開兩張表，不共用同一個 status 欄位
    status = Column(String, nullable=False, default="pending")
    total_amount = Column(Numeric(10, 2), nullable=False)
    # 這筆訂單折抵用掉的點數／折抵金額，做法比照 Order（0 代表沒有使用點數）
    points_used = Column(Integer, nullable=False, server_default="0")
    points_discount = Column(Numeric(10, 2), nullable=False, server_default="0")
    # 2026-09 優惠券線上折抵，做法比照 Order.coupon_id/coupon_discount（見該處註解）
    coupon_id = Column(Integer, ForeignKey("coupons.id"), nullable=True)
    coupon_discount = Column(Numeric(10, 2), nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="dine_in_orders")
    items = relationship(
        "DineInOrderItem", back_populates="order", cascade="all, delete-orphan"
    )


class DineInOrderItem(Base):
    __tablename__ = "dine_in_order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(
        Integer, ForeignKey("dine_in_orders.id"), index=True, nullable=False
    )
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    # 下單當下的價格快照，避免之後菜單改價影響歷史訂單金額（比照 OrderItem 的做法）
    unit_price = Column(Numeric(10, 2), nullable=False)
    subtotal = Column(Numeric(10, 2), nullable=False)

    order = relationship("DineInOrder", back_populates="items")
    menu_item = relationship("MenuItem", back_populates="dine_in_order_items")


class Restaurant(Base):
    """
    門市／分店（2026-09 跟 mynotification/staff-scanner 三方確認上線的多門市支援）。
    範圍只涵蓋堂食點餐（Table/MenuItem/DineInOrder/staff 帳號），網購商店
    （Product/Order）刻意不跟著改——業務確認網購是集中倉儲，不需要綁定門市。
    """

    __tablename__ = "restaurants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Table(Base):
    __tablename__ = "tables"
    __table_args__ = (
        UniqueConstraint("restaurant_id", "code", name="uq_tables_restaurant_code"),
    )

    id = Column(Integer, primary_key=True, index=True)
    # 門市歸屬。nullable 是為了向下相容多門市上線前建立的既有資料（沒有刻意
    # 遷移/捨棄舊資料，見 CLAUDE.md 多門市一節）；新建的桌位一律要求帶這個欄位
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), index=True, nullable=True)
    # 桌號字串（例如 "A3"），店員 App 拿這個組 QR Code deep link；
    # 改成「同一門市內 unique」而不是全域 unique（不同門市可以各自有自己的 "A3"）
    code = Column(String, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LoyaltyTransaction(Base):
    __tablename__ = "loyalty_transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    # earn（賺取）/ redeem（折抵）/ expire（過期收回）/ reverse（訂單取消退款收回或退還）
    type = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)  # 正整數，異動方向由 type 決定
    # 只有 type="earn" 的列會用到：初始等於 amount，之後被 redeem 依到期日 FIFO
    # 消耗時遞減；到期排程只需要收走還沒被消耗掉的部分，不用重算整包歷史
    remaining_amount = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime(timezone=True), nullable=True)  # 只有 type="earn" 會設值
    reason = Column(String, nullable=False)  # 人類可讀說明，例如「消費回饋：訂單 #123」
    related_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    related_dine_in_order_id = Column(
        Integer, ForeignKey("dine_in_orders.id"), nullable=True
    )
    # 2026-09 門市收銀（會員條碼結帳）新增的第三個 related_X_id，跟前兩個對稱，
    # 讓每種消費來源都能單獨追溯，不共用既有欄位混著記
    related_store_checkout_id = Column(
        Integer, ForeignKey("store_checkouts.id"), nullable=True
    )
    # 2026-09 多門市支援：純記錄/報表用途，標記這筆異動發生在哪個門市（堂食訂單
    # 才有；網購訂單、連鎖層級的禮券發點等沒有門市脈絡就是 NULL）。**不影響餘額
    # 計算或折抵資格**——點數餘額仍是 User.loyalty_balance 單一帳戶層級，任何門市
    # 賺的點都能在任何門市折抵，這欄位只是讓「這筆消費/折抵是哪個門市」可追溯。
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="loyalty_transactions")


class StoreCheckout(Base):
    """
    2026-09 會員條碼/QR Code 門市收銀：顧客出示會員碼，店員在 staff-scanner 輸入
    金額、選付款方式（現金/街口支付，皆視為當下已完成付款，沒有像 ECPay 那樣的
    非同步中繼狀態，所以不需要 status="pending" 之類的中繼態或取消/退還機制）。
    刻意獨立成一張表、不塞進 Order——Order 幾乎每個欄位（merchant_trade_no、
    payment_reference、OrderItem 的 product_id NOT NULL）都是繞著 ECPay 線上金流
    +商品項目設計的，門市收銀完全沒有這些概念，硬塞會出現一堆語意不明的 nullable
    欄位，跟當初 DineInOrder 沒有沿用 Order 是同一個理由。
    """

    __tablename__ = "store_checkouts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    # 處理這筆結帳的店員——跟 Order/DineInOrder 不同，那兩個是顧客自助操作，
    # 這裡是店員代替顧客操作金流，事後要能追溯是哪位店員經手
    staff_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    # 全新的表、沒有既有資料要相容，這裡直接設 nullable=False——送出結帳的
    # 端點本來就要求登入店員必須已被指定門市（帳號尚未指定所屬門市回 400），
    # 不會有 restaurant_id 缺值的合法情境
    restaurant_id = Column(Integer, ForeignKey("restaurants.id"), index=True, nullable=False)
    # 店員手動輸入的原始金額——這裡沒有商品/庫存可以當金額的權威來源，
    # 跟 Order/DineInOrder 由後端依商品價格重新計算不同，是刻意的信任層級
    # （比照 PATCH /products/{id} 店員可以直接改價格/庫存）
    subtotal = Column(Numeric(10, 2), nullable=False)
    # "cash" / "jkopay"，純字串沒有 DB 層 enum，比照 Order.status 慣例
    payment_method = Column(String, nullable=False)
    coupon_id = Column(Integer, ForeignKey("coupons.id"), nullable=True)
    coupon_discount = Column(Numeric(10, 2), nullable=False, server_default="0")
    points_used = Column(Integer, nullable=False, server_default="0")
    points_discount = Column(Numeric(10, 2), nullable=False, server_default="0")
    # 折抵後實付金額，付款當下就是最終值（沒有中繼付款狀態）
    total_amount = Column(Numeric(10, 2), nullable=False)
    # 先固定 "completed"，多留這個欄位是為了之後如果要做「店員打錯金額作廢」
    # 這類需求時不用再 migration，目前沒有任何程式碼會寫入其他值
    status = Column(String, nullable=False, server_default="completed")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", foreign_keys=[user_id])
    staff_user = relationship("User", foreign_keys=[staff_user_id])


class MagicLinkToken(Base):
    __tablename__ = "magic_link_tokens"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    # 只存 token 的 hash，不存明文；就算資料庫外洩也無法重放
    token_hash = Column(String, unique=True, index=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)  # 已使用時間，None 代表尚未使用
    created_at = Column(DateTime(timezone=True), server_default=func.now())


## alembic 更新DB作法:
# 1. 編輯 models.py 定義好 ORM 類別
# 2. 執行 alembic revision --autogenerate -m "新增User 和 Notificationlog relationship 1"
# 3. 執行 alembic upgrade head
