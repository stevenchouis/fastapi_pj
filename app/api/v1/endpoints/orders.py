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
from app.models import LoyaltyTransaction, Order, OrderItem
from app.models import Product as ProductModel
from app.schemas.order import (
    OrderCheckoutOut,
    OrderCreate,
    OrderItemOut,
    OrderOut,
    OrderStatusUpdate,
)
from app.core.monitoring import capture_exception, capture_message
from app.services import coupon_service, ecpay_service, loyalty_service, order_service
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


async def _to_orders_out(db: AsyncSession, orders: list[Order]) -> list[OrderOut]:
    # points_earned 不是存在 DB 的欄位：2026-11 會員等級上線後，入帳點數取決於
    # 當時的等級倍率，不能再用 calc_earned_points(total) 重算，改成直接加總
    # LoyaltyTransaction 裡這張訂單實際入帳的 earn 點數（一次查完，避免 N+1）
    earned = await loyalty_service.earned_points_by_related(
        db, LoyaltyTransaction.related_order_id, [o.id for o in orders]
    )
    return [_to_order_out(o, earned.get(o.id, 0)) for o in orders]


def _to_order_out(order: Order, points_earned: int = 0) -> OrderOut:
    return OrderOut(
        id=order.id,
        status=order.status,
        total_amount=float(order.total_amount),
        coupon_id=order.coupon_id,
        coupon_discount=float(order.coupon_discount),
        points_used=order.points_used,
        points_discount=float(order.points_discount),
        points_earned=points_earned,
        einvoice_carrier=order.einvoice_carrier,
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
    return await _to_orders_out(db, orders)


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
            # 2026-11 電子發票載具：快照會員當下的載具（前端不傳），之後改載具不影響這筆訂單
            einvoice_carrier=current_user.einvoice_carrier,
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
    return (await _to_orders_out(db, [order]))[0]


@router.post("/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    使用者主動取消尚未付款的訂單（僅限自己名下、status="pending"）。

    背景：coupon_id/use_points 是在建立訂單當下就直接生效（優惠券標記已使用、
    點數已扣除、庫存已扣減），但如果之後付款沒有成功（ECPay 顯示錯誤、使用者
    中途放棄），訂單會卡在 pending，而券/點數/庫存已經回不去——這支端點負責
    在使用者主動取消時把三者都退還：優惠券改回未使用（coupon_service.release_coupon）、
    點數用 reverse_redeem 交易補回餘額（loyalty_service.reverse_redeem_points）、
    商品庫存加回去，訂單狀態改成 cancelled。
    """
    # 2026-11：鎖住訂單列再檢查狀態，避免跟 ECPay callback／逾時取消排程同時處理而退兩次
    order = await order_service.lock_order(db, order_id)
    if order is None or order.user_id != current_user.id:
        await db.rollback()
        raise HTTPException(status_code=404, detail="訂單不存在")
    if order.status != "pending":
        await db.rollback()
        raise HTTPException(status_code=409, detail="只有待付款的訂單可以取消")

    try:
        await order_service.release_order_holds(db, order, reason=f"訂單取消退還：訂單 #{order_id}")
        order.status = "cancelled"
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 取消訂單失敗: {e}")
        raise HTTPException(status_code=500, detail="取消訂單失敗")

    query = select(Order).where(Order.id == order_id).options(ORDER_LOAD_OPTIONS)
    result = await db.execute(query)
    return (await _to_orders_out(db, [result.scalars().first()]))[0]


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

    2026-09-15 實機測試發現：ECPay 會拒絕重複送出同一個 MerchantTradeNo（顯示「訂單編號
    重複」），所以不能像原本那樣每次都沿用 Order.merchant_trade_no 建立當下那組固定值——
    只要同一筆訂單重打過一次這支（不論是使用者主動點「繼續付款」重試，還是畫面重複觸發），
    第二次一定會被 ECPay 擋下。改成每次呼叫都重新產生一組新的 MerchantTradeNo 並覆蓋回
    Order.merchant_trade_no（Order.id 才是我方永遠不變的內部主鍵），/ecpay/callback
    永遠用「當下這組」merchant_trade_no 反查訂單，不假設訂單一輩子只有一個固定值。
    這裡先在 commit 前就用記憶體中已更新好新值的 order 物件組付款欄位，避免 commit 後
    session 把屬性 expire 掉又要重新查詢的 MissingGreenlet 坑。

    已知的殘餘風險（低機率，比照 ecpay_callback 對「訂單已取消」的處理方式，選擇記錄而非
    完全杜絕）：如果使用者對同一筆訂單前後打開兩個付款頁（例如第一個沒關掉又重試一次），
    舊的那組 MerchantTradeNo 之後才被使用者拿去完成付款，callback 進來時會因為訂單已經
    被覆寫成新的 MerchantTradeNo 而查無對應訂單，回應 "0|OrderNotFound"（已有 log 記錄，
    需要人工核對）。正常情境下重試就是放棄舊付款頁，這個風險機率很低。
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

    order.merchant_trade_no = _generate_merchant_trade_no()
    fields = ecpay_service.build_checkout_params(order)

    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 更新 MerchantTradeNo 失敗: {e}")
        raise HTTPException(status_code=500, detail="建立付款失敗，請重試")

    return OrderCheckoutOut(action_url=settings.ECPAY_ACTION_URL, fields=fields)


@router.post("/ecpay/callback")
async def ecpay_callback(
    request: Request,
    background_tasks: BackgroundTasks,
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

    # 2026-11：綠界後台「模擬付款」送來的通知（SimulatePaid=1）不是真的收到錢，
    # 不改狀態、不發點數，只留紀錄
    if params.get("SimulatePaid") == "1":
        print(f"WARNING: 收到 ECPay 模擬付款通知（{merchant_trade_no}），不處理")
        return PlainTextResponse("1|OK")

    order_id = (
        await db.execute(select(Order.id).where(Order.merchant_trade_no == merchant_trade_no))
    ).scalar()
    if order_id is None:
        print(f"DEBUG: ECPay callback 找不到對應訂單: {merchant_trade_no}")
        return PlainTextResponse("0|OrderNotFound")

    # 2026-11：鎖住訂單列再判斷狀態，避免跟使用者取消／逾時取消排程同時處理
    order = await order_service.lock_order(db, order_id)

    if order.status != "pending":
        # 已處理過（paid/shipped），或已經取消／失敗（庫存、優惠券、點數都已退回）。
        # 重送的通知一律回 "1|OK"，不重複處理。取消／失敗後才收到付款成功，代表錢可能
        # 已經入帳但跟我方狀態對不上：不能再標成 paid（等於券／點數退了又多一筆已付款訂單），
        # 也不能默默吞掉——印 WARNING 並送 Sentry，留給人工核對是否要退款。
        status_now = order.status
        await db.rollback()
        if params.get("RtnCode") == "1" and status_now in ("cancelled", "failed"):
            message = (
                f"ECPay callback 回報付款成功，但訂單 #{order_id}（{merchant_trade_no}）"
                f"已是 {status_now}，需人工核對是否要退款"
            )
            print(f"WARNING: {message}")
            capture_message(message, level="warning", area="ecpay")
        return PlainTextResponse("1|OK")

    if params.get("RtnCode") == "1":
        # 2026-11：金額要跟我方送出的 TotalAmount 一致，不一致不改狀態，回非 1|OK 讓綠界重送、留下痕跡
        expected = ecpay_service.expected_trade_amount(order)
        if params.get("TradeAmt") != str(expected):
            await db.rollback()
            message = (
                f"ECPay callback 金額不符：訂單 #{order_id}（{merchant_trade_no}）"
                f"TradeAmt={params.get('TradeAmt')}，應為 {expected}"
            )
            print(f"WARNING: {message}")
            capture_message(message, level="error", area="ecpay")
            return PlainTextResponse("0|AmountMismatch")

    tier_result = None
    order_user_id = order.user_id
    try:
        if params.get("RtnCode") == "1":
            order.status = "paid"
            order.payment_reference = params.get("TradeNo")
            order.paid_at = datetime.now(UTC)

            # 2026-11 會員等級：倍率、升等判斷與升等禮券都在 process_tier_earn 裡
            tier_result = await loyalty_service.process_tier_earn(
                db,
                order_user_id,
                order.total_amount,
                reason_prefix=f"網購訂單 #{order.id} 消費回饋",
                related_order_id=order.id,
            )
        else:
            # 2026-11：付款失敗也要把建立訂單時佔用的庫存／優惠券／點數退回（比照取消）
            order.status = "failed"
            await order_service.release_order_holds(
                db, order, reason=f"付款失敗退還：訂單 #{order_id}"
            )

        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 處理 ECPay callback 失敗: {e}")
        capture_exception(e, area="ecpay")
        return PlainTextResponse("0|ProcessError")

    # 升等推播／推薦獎勵推播（commit 之後才排）
    loyalty_service.queue_reward_pushes(background_tasks, order_user_id, tier_result)

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
    return await _to_orders_out(db, orders)


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
    return (await _to_orders_out(db, [order]))[0]
