# app/api/v1/endpoints/dine_in_orders.py
from decimal import Decimal
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.database_async import AsyncSessionLocal, get_db
from app.models import DineInOrder, DineInOrderItem
from app.models import MenuItem as MenuItemModel
from app.schemas.dine_in_order import (
    DineInOrderCreate,
    DineInOrderItemOut,
    DineInOrderOut,
)
from app.services.push_service import send_role_push_notifications

router = APIRouter()

DINE_IN_ORDER_LOAD_OPTIONS = selectinload(DineInOrder.items).selectinload(
    DineInOrderItem.menu_item
)


def _to_order_out(order: DineInOrder) -> DineInOrderOut:
    return DineInOrderOut(
        id=order.id,
        table_number=order.table_number,
        status=order.status,
        total_amount=float(order.total_amount),
        created_at=order.created_at,
        items=[
            DineInOrderItemOut(
                menu_item_id=item.menu_item_id,
                name=item.menu_item.name,
                quantity=item.quantity,
                unit_price=float(item.unit_price),
                subtotal=float(item.subtotal),
            )
            for item in order.items
        ],
    )


@router.post("", response_model=DineInOrderOut, status_code=201)
async def create_dine_in_order(
    payload: DineInOrderCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    建立堂食點餐訂單。跟網購 /orders 是分開的流程：這裡沒有庫存概念（賣完由店員
    手動關閉 is_available），所以不需要原子性扣庫存，但價格一律以資料庫當下的值
    為準，不採信前端顯示的金額；桌號是前端自由文字輸入，後端不做格式驗證或查詢。
    """
    # 同一品項在同一次點餐中出現多次時先合併數量
    quantities: dict[int, int] = {}
    for item in payload.items:
        quantities[item.menu_item_id] = (
            quantities.get(item.menu_item_id, 0) + item.quantity
        )

    query = select(MenuItemModel).where(
        MenuItemModel.id.in_(quantities.keys()),
        MenuItemModel.is_available.is_(True),
    )
    result = await db.execute(query)
    menu_items = {menu_item.id: menu_item for menu_item in result.scalars().all()}

    missing_ids = set(quantities.keys()) - set(menu_items.keys())
    if missing_ids:
        raise HTTPException(
            status_code=409,
            detail=f"品項 {sorted(missing_ids)} 不存在或已下架",
        )

    order_items: List[DineInOrderItem] = []
    total_amount = Decimal("0")
    for menu_item_id, quantity in quantities.items():
        menu_item = menu_items[menu_item_id]
        subtotal = menu_item.price * quantity
        total_amount += subtotal
        order_items.append(
            DineInOrderItem(
                menu_item_id=menu_item_id,
                quantity=quantity,
                unit_price=menu_item.price,
                subtotal=subtotal,
            )
        )

    try:
        order = DineInOrder(
            user_id=current_user.id,
            table_number=payload.table_number,
            status="pending",
            total_amount=total_amount,
        )
        order.items = order_items
        db.add(order)
        # 先 flush 拿到 id（此時屬性還沒過期），commit 後 session 預設會
        # expire 掉所有屬性，之後再存取 order.id 會觸發同步環境下無法完成的
        # 非同步重新查詢（MissingGreenlet），所以要在 commit 前存成區域變數
        await db.flush()
        order_id = order.id
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 建立堂食訂單失敗: {e}")
        raise HTTPException(status_code=500, detail="建立訂單失敗")

    query = (
        select(DineInOrder)
        .where(DineInOrder.id == order_id)
        .options(DINE_IN_ORDER_LOAD_OPTIONS)
    )
    result = await db.execute(query)
    order = result.scalars().first()

    # 送出訂單後推播通知所有店員（role="staff"），讓他們知道有新訂單要備餐；
    # 比照 push_service 既有慣例，session 已經 commit 完才觸發，不佔用交易時間
    background_tasks.add_task(
        send_role_push_notifications,
        AsyncSessionLocal,
        "staff",
        "🍽️ 新的堂食訂單",
        f"桌號 {order.table_number} 送出新訂單",
        {"screen": "DineInOrders", "dine_in_order_id": order_id},
    )

    return _to_order_out(order)


@router.get("/me", response_model=List[DineInOrderOut])
async def get_my_dine_in_orders(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """取得目前登入使用者的堂食點餐紀錄（含明細），新到舊排序。"""
    query = (
        select(DineInOrder)
        .where(DineInOrder.user_id == current_user.id)
        .options(DINE_IN_ORDER_LOAD_OPTIONS)
        .order_by(DineInOrder.created_at.desc())
    )
    result = await db.execute(query)
    orders = result.scalars().all()
    return [_to_order_out(order) for order in orders]
