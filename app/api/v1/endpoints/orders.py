# app/api/v1/endpoints/orders.py
import secrets
from datetime import UTC, datetime
from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.database_async import get_db
from app.models import Order, OrderItem
from app.models import Product as ProductModel
from app.schemas.order import OrderCreate, OrderItemOut, OrderOut

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
    return OrderOut(
        id=order.id,
        status=order.status,
        total_amount=float(order.total_amount),
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
    （已扣的其他項目一併 rollback，不會賣出部分商品卻沒建立訂單）。

    注意：金流（綠界 ECPay）串接尚未完成——這裡只建立 pending 狀態的訂單並扣庫存，
    之後要補上呼叫 ECPay Checkout 頁面、以及付款完成 callback 驗簽、更新
    status/payment_reference/paid_at 的步驟。
    """
    # 同一商品在同一次下單中出現多次時先合併數量，避免重複扣庫存判斷失準
    quantities: dict[int, int] = {}
    for item in payload.items:
        quantities[item.product_id] = (
            quantities.get(item.product_id, 0) + item.quantity
        )

    order_items: List[OrderItem] = []
    total_amount = Decimal("0")

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
            subtotal = price * quantity
            total_amount += subtotal
            order_items.append(
                OrderItem(
                    product_id=product_id,
                    quantity=quantity,
                    unit_price=price,
                    subtotal=subtotal,
                )
            )

        order = Order(
            user_id=current_user.id,
            status="pending",
            total_amount=total_amount,
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
