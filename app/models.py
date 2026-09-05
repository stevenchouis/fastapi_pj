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


class PushToken(Base):
    __tablename__ = "push_tokens"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    token = Column(
        String, unique=True, index=True, nullable=False
    )  # 儲存 ExpoPushToken[xxx...]
    device_name = Column(String, nullable=True)  # 可選：辨識裝置類型 (如 "iPhone 15")
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
