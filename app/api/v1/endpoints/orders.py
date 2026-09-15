# app/api/v1/endpoints/orders.py
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from typing import List
from urllib.parse import parse_qsl

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.core.config import settings
from app.database_async import AsyncSessionLocal, get_db
from app.models import Order, OrderItem
from app.models import Product as ProductModel
from app.schemas.order import (
    OrderCheckoutOut,
    OrderCreate,
    OrderItemOut,
    OrderOut,
    OrderStatusUpdate,
)
from app.services import coupon_service, ecpay_service, loyalty_service
from app.services.push_service import send_user_push_notifications

router = APIRouter()

ORDER_LOAD_OPTIONS = selectinload(Order.items).selectinload(OrderItem.product)


def _generate_merchant_trade_no() -> str:
    """
    ECPay 要求 MerchantTradeNo 只能是英數字、長度上限 20 碼，且需商店內唯一。
    用時間戳（到秒）+ 2 bytes 隨機碼組出 17 碼，同一秒內撞號機率極低；
    真的撞號時會被資料庫 unique 限制擋下，走下面的 500 錯誤處理重試即可。
    """
    return f"O{datetime.now(UTC).strftime('%y%m%d%H%M%S')}{secrets.token_hex(2)}"


def _to_order_out(order: Order) -> OrderOut:
    # points_earned 是算出來的（不是存在 DB 的欄位），跟實際發點邏輯共用同一個
    # calc_earned_points，確保顯示數字跟 LoyaltyTransaction 真正入帳的數字一致。
    # "shipped" 也算——出貨只是付款完成後的後續狀態，earn_points 是在 status 變成
    # "paid" 那一刻就已經真的發放了，不是等出貨才發，這裡只是沿用已經發生過的事實。
    points_earned = (
        loyalty_service.calc_earned_points(order.total_amount)
        if order.status in ("paid", "shipped")
        else 0
    )
    return OrderOut(
        id=order.id,
        status=order.status,
        total_amount=float(order.total_amount),
        coupon_id=order.coupon_id,
        coupon_discount=float(order.coupon_discount),
        points_used=order.points_used,
        points_discount=float(order.points_discount),
        points_earned=points_earned,
        payment_provider=order.payment_provider,
        merchant_trade_no=order.merchant_trade_no,
        created_at=order.created_at,
        paid_at=order.paid_at,
        items=[
            OrderItemOut(
                product_id=item.product_id,
                title=item.product.title,
                quantity=item.quantity,
                unit_price=float(item.unit_price),
                subtotal=float(item.subtotal),
            )
            for item in order.items
        ],
    )


@router.get("/me", response_model=List[OrderOut])
async def get_my_orders(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """取得目前登入使用者的訂單列表（含明細），新到舊排序。"""
    query = (
        select(Order)
        .where(Order.user_id == current_user.id)
        .options(ORDER_LOAD_OPTIONS)
        .order_by(Order.created_at.desc())
    )
    result = await db.execute(query)
    orders = result.scalars().all()
    return [_to_order_out(order) for order in orders]


@router.post("", response_model=OrderOut, status_code=201)
async def create_order(
    payload: OrderCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    建立訂單。price/stock 一律以後端這次重新查到的資料為準，不採信前端傳入的金額；
    庫存用「UPDATE ... WHERE stock >= 數量」原子性扣減，任一項商品庫存不足就整張訂單失敗
    （已扣的其他項目一併 rollback，不會賣出部分商品卻沒建立訂單）。可選的 coupon_id／
    use_points 皆可折抵訂單金額，順序是先套用優惠券折扣（clamp 到不超過商品小計）、
    再用「券後金額」計算點數折抵上限（1 點 = NT$1，上限券後金額 50%）——這樣兩者疊加
    後 total_amount 保證不會是負數。優惠券折抵是直接生效（不產生核銷碼），跟到店核銷
    是不同通路，共用同一個 Coupon.is_used 欄位的原子性更新，兩邊不會雙重折抵（詳見
    coupon_service.apply_coupon_for_checkout）。驗證/扣點/扣券都跟建立訂單包在同一個
    transaction，任一步失敗就整單 rollback。

    這裡只建立 pending 狀態的訂單並扣庫存；金流動作（叫出付款頁、驗證付款結果）
    是後續呼叫 POST /{order_id}/checkout 跟 ECPay 打 POST /ecpay/callback 才會發生，
    消費回饋點數（earn）也是等 callback 確認 RtnCode=1 才會真的觸發。
    """
    # 同一商品在同一次下單中出現多次時先合併數量，避免重複扣庫存判斷失準
    quantities: dict[int, int] = {}
    for item in payload.items:
        quantities[item.product_id] = (
            quantities.get(item.product_id, 0) + item.quantity
        )

    order_items: List[OrderItem] = []
    subtotal = Decimal("0")

    try:
        for product_id, quantity in quantities.items():
            statement = (
                update(ProductModel)
                .where(ProductModel.id == product_id)
                .where(ProductModel.is_active.is_(True))
                .where(ProductModel.stock >= quantity)
                .values(stock=ProductModel.stock - quantity)
                .returning(ProductModel.price)
            )
            result = await db.execute(statement)
            row = result.first()
            if row is None:
                await db.rollback()
                raise HTTPException(
                    status_code=409, detail=f"商品 {product_id} 庫存不足或已下架"
                )
            (price,) = row
            item_subtotal = price * quantity
            subtotal += item_subtotal
            order_items.append(
                OrderItem(
                    product_id=product_id,
                    quantity=quantity,
                    unit_price=price,
                    subtotal=item_subtotal,
                )
            )

        coupon_discount = Decimal("0")
        if payload.coupon_id is not None:
            outcome, discount_amount = await coupon_service.apply_coupon_for_checkout(
                db, current_user.id, payload.coupon_id
            )
            if outcome == "not_found":
                await db.rollback()
                raise HTTPException(status_code=404, detail="優惠券不存在")
            if outcome == "already_used":
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "coupon_already_used",
                        "message": "此優惠券已使用",
                    },
                )
            if outcome == "expired":
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "coupon_expired",
                        "message": "此優惠券已過期",
                    },
                )
            coupon_discount = min(discount_amount, subtotal)

        after_coupon = subtotal - coupon_discount

        points_used = payload.use_points
        points_discount = Decimal("0")
        if points_used > 0:
            max_points = loyalty_service.calc_max_redeemable_points(after_coupon)
            if points_used > max_points:
                await db.rollback()
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error_code": "points_cap_exceeded",
                        "message": f"超過可折抵金額 50% 上限，最多可用 {max_points} 點",
                    },
                )
            points_discount = Decimal(points_used) * loyalty_service.REDEEM_POINT_VALUE

        order = Order(
            user_id=current_user.id,
            status="pending",
            total_amount=after_coupon - points_discount,
            coupon_id=payload.coupon_id,
            coupon_discount=coupon_discount,
            points_used=points_used,
            points_discount=points_discount,
            payment_provider="ecpay",
            merchant_trade_no=_generate_merchant_trade_no(),
        )
        order.items = order_items
        db.add(order)
        # 先 flush 拿到 id（此時屬性還沒過期），commit 後 session 預設會
        # expire 掉所有屬性，之後再存取 order.id 會觸發同步環境下無法完成的
        # 非同步重新查詢（MissingGreenlet），所以要在 commit 前存成區域變數
        await db.flush()
        order_id = order.id

        if points_used > 0:
            redeemed = await loyalty_service.redeem_points(
                db,
                current_user.id,
                points_used,
                reason=f"折抵：訂單 #{order_id}",
                related_order_id=order_id,
            )
            if not redeemed:
                await db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error_code": "insufficient_points",
                        "message": "點數餘額不足",
                    },
                )

        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 建立訂單失敗: {e}")
        raise HTTPException(status_code=500, detail="建立訂單失敗")

    query = select(Order).where(Order.id == order_id).options(ORDER_LOAD_OPTIONS)
    result = await db.execute(query)
    order = result.scalars().first()
    return _to_order_out(order)


@router.post("/{order_id}/checkout", response_model=OrderCheckoutOut)
async def create_order_checkout(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    組出 ECPay AioCheckOut/V5 需要的付款表單欄位（含 CheckMacValue），前端拿去在 WebView
    組表單 POST 到 action_url。跟建立訂單分開成獨立端點，讓使用者中途關掉付款頁時
    可以重打這支重新叫出付款頁，不需要重新建立訂單、重複扣庫存。
    """
    query = (
        select(Order)
        .where(Order.id == order_id, Order.user_id == current_user.id)
        .options(ORDER_LOAD_OPTIONS)
    )
    result = await db.execute(query)
    order = result.scalars().first()
    if order is None:
        raise HTTPException(status_code=404, detail="訂單不存在")
    if order.status != "pending":
        raise HTTPException(status_code=409, detail="訂單狀態不是待付款，無法建立付款")

    fields = ecpay_service.build_checkout_params(order)
    return OrderCheckoutOut(action_url=settings.ECPAY_ACTION_URL, fields=fields)


@router.post("/ecpay/callback")
async def ecpay_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    ECPay Server-to-Server 付款結果通知（ReturnURL）。沒有 JWT——是 ECPay 直接呼叫，
    沒有使用者 session。驗簽失敗／找不到訂單都回應非 "1|OK"，讓 ECPay 依它自己的重試機制
    再送一次；已經處理過的訂單（status 已是 paid）直接回 "1|OK"，避免 ECPay 重送造成
    重複發放點數。回應格式（純文字 "1|OK"）是 ECPay 的固定規定，不能改。

    2026-09-14 實機測試發現：用 request.form()（底層是 python-multipart 的串流解析器）
    在 Render 上偶爾會把 RtnMsg 這類含中文的欄位解碼成 Latin-1 亂碼（本機用 TestClient
    無法重現，懷疑是真實網路環境下分段傳輸、逐段解碼 percent-encoding 時踩到 edge case），
    中文欄位本身雖然不影響簽章正確性判斷的邏輯，但它也是 CheckMacValue 簽章涵蓋的欄位之一，
    解碼錯了會導致我方重算的雜湊對不上 ECPay 送來的值，簽章驗證間歇性失敗。改成先用
    request.body() 把完整原始 bytes 一次讀完，再用標準庫 parse_qsl 一次性解碼，
    避開任何逐段解析可能踩到的 edge case。
    """
    raw_body = await request.body()
    params = dict(
        parse_qsl(raw_body.decode("utf-8"), encoding="utf-8", keep_blank_values=True)
    )

    if not ecpay_service.verify_check_mac_value(
        params, settings.ECPAY_HASH_KEY, settings.ECPAY_HASH_IV
    ):
        print(f"DEBUG: ECPay callback 簽章驗證失敗: {params}")
        return PlainTextResponse("0|CheckMacValueError")

    merchant_trade_no = params.get("MerchantTradeNo")
    query = select(Order).where(Order.merchant_trade_no == merchant_trade_no)
    result = await db.execute(query)
    order = result.scalars().first()
    if order is None:
        print(f"DEBUG: ECPay callback 找不到對應訂單: {merchant_trade_no}")
        return PlainTextResponse("0|OrderNotFound")

    if order.status == "paid":
        return PlainTextResponse("1|OK")

    try:
        if params.get("RtnCode") == "1":
            order.status = "paid"
            order.payment_reference = params.get("TradeNo")
            order.paid_at = datetime.now(UTC)

            earned_points = loyalty_service.calc_earned_points(order.total_amount)
            if earned_points > 0:
                await loyalty_service.earn_points(
                    db,
                    order.user_id,
                    earned_points,
                    reason=f"網購訂單 #{order.id} 消費回饋",
                    related_order_id=order.id,
                )
        else:
            order.status = "failed"

        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 處理 ECPay callback 失敗: {e}")
        return PlainTextResponse("0|ProcessError")

    return PlainTextResponse("1|OK")


@router.get("", response_model=List[OrderOut])
async def list_orders_for_staff(
    status: str = "paid",
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """
    店員出貨管理列表（`role="staff"`）。網購商店（`Product`/`Order`）刻意沒有門市概念
    （集中倉儲，見 CLAUDE.md），所以這支不像堂食訂單列表會自動 scope 到店員自己的門市，
    是跨門市看全部訂單。預設篩 `status="paid"`（等待出貨的訂單），依 `created_at`
    舊到新排序（FIFO，比照堂食接單列表）。
    """
    query = (
        select(Order)
        .where(Order.status == status)
        .options(ORDER_LOAD_OPTIONS)
        .order_by(Order.created_at.asc())
    )
    result = await db.execute(query)
    orders = result.scalars().all()
    return [_to_order_out(order) for order in orders]


@router.patch("/{order_id}/status", response_model=OrderOut)
async def update_order_status(
    order_id: int,
    payload: OrderStatusUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """
    店員標記訂單已出貨（`role="staff"`）。目前只開放 status="shipped" 這一個目標值
    （schema 用 Literal 限制，帶其他值回 422），只允許從 "paid" 轉過去，找不到訂單回 404，
    訂單不是 "paid" 狀態（還沒付款、已出貨過、失敗、取消）回 409。
    """
    query = select(Order).where(Order.id == order_id).options(ORDER_LOAD_OPTIONS)
    result = await db.execute(query)
    order = result.scalars().first()
    if order is None:
        raise HTTPException(status_code=404, detail="訂單不存在")
    if order.status != "paid":
        raise HTTPException(status_code=409, detail="只有已付款的訂單可以標記出貨")

    order.status = payload.status
    # commit 後 session 預設會把物件所有屬性標記為過期，之後不能再碰 order 的任何屬性
    # （不管是 order.items 這種關聯、還是 order.user_id 這種純欄位）——會觸發同步環境下
    # 無法完成的非同步重新查詢，噴 MissingGreenlet（CLAUDE.md 記錄過的同一個坑）。
    # 需要的值先存成區域變數，commit 後只用區域變數、重新查詢一次取得完整關聯。
    order_user_id = order.user_id

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 標記訂單出貨失敗: {e}")
        raise HTTPException(status_code=500, detail="標記出貨失敗")

    # 比照 push_service 既有慣例，session 已經 commit 完才觸發，不佔用交易時間
    background_tasks.add_task(
        send_user_push_notifications,
        AsyncSessionLocal,
        order_user_id,
        "mynotification",
        "您的訂單已出貨",
        f"訂單 #{order_id} 已出貨，敬請留意配送進度。",
        {"type": "order_shipped", "screen": "OrderDetail", "order_id": order_id},
    )

    query = select(Order).where(Order.id == order_id).options(ORDER_LOAD_OPTIONS)
    result = await db.execute(query)
    order = result.scalars().first()
    return _to_order_out(order)
