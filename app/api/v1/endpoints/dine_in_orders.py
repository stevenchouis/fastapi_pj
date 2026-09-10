# app/api/v1/endpoints/dine_in_orders.py
from decimal import Decimal
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
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
    DineInOrderStatusUpdate,
)
from app.services import loyalty_service
from app.services.push_service import send_role_push_notifications

router = APIRouter()

DINE_IN_ORDER_LOAD_OPTIONS = selectinload(DineInOrder.items).selectinload(
    DineInOrderItem.menu_item
)


def _to_order_out(order: DineInOrder) -> DineInOrderOut:
    # points_earned 是算出來的（不是存在 DB 的欄位），跟實際發點邏輯
    # （update_dine_in_order_status）用同一個判斷條件、同一個換算函式，
    # 確保這裡顯示的數字跟 LoyaltyTransaction 裡真正入帳的數字一致
    points_earned = (
        loyalty_service.calc_earned_points(order.total_amount)
        if order.status == "completed"
        else 0
    )
    return DineInOrderOut(
        id=order.id,
        table_number=order.table_number,
        status=order.status,
        total_amount=float(order.total_amount),
        points_used=order.points_used,
        points_discount=float(order.points_discount),
        points_earned=points_earned,
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
    可選的 use_points 用來折抵訂單金額，規則跟 /orders 相同（1 點 = NT$1，上限訂單
    小計 50%），驗證/扣點都跟建立訂單包在同一個 transaction。
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
    subtotal = Decimal("0")
    for menu_item_id, quantity in quantities.items():
        menu_item = menu_items[menu_item_id]
        item_subtotal = menu_item.price * quantity
        subtotal += item_subtotal
        order_items.append(
            DineInOrderItem(
                menu_item_id=menu_item_id,
                quantity=quantity,
                unit_price=menu_item.price,
                subtotal=item_subtotal,
            )
        )

    points_used = payload.use_points
    points_discount = Decimal("0")
    if points_used > 0:
        max_points = loyalty_service.calc_max_redeemable_points(subtotal)
        if points_used > max_points:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "points_cap_exceeded",
                    "message": f"超過訂單金額 50% 折抵上限，最多可用 {max_points} 點",
                },
            )
        points_discount = Decimal(points_used) * loyalty_service.REDEEM_POINT_VALUE

    try:
        order = DineInOrder(
            user_id=current_user.id,
            table_number=payload.table_number,
            status="pending",
            total_amount=subtotal - points_discount,
            points_used=points_used,
            points_discount=points_discount,
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
                reason=f"折抵：堂食訂單 #{order_id}",
                related_dine_in_order_id=order_id,
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
        "staff-scanner",
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


@router.get("", response_model=List[DineInOrderOut])
async def list_dine_in_orders(
    status: str = Query(default="pending"),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """
    店員接單列表，只有 role="staff" 能呼叫。預設只列 pending 狀態，
    依 created_at 舊到新排序（FIFO，先送的單先出餐）。
    """
    query = (
        select(DineInOrder)
        .where(DineInOrder.status == status)
        .options(DINE_IN_ORDER_LOAD_OPTIONS)
        .order_by(DineInOrder.created_at.asc())
    )
    result = await db.execute(query)
    orders = result.scalars().all()
    return [_to_order_out(order) for order in orders]


@router.patch("/{dine_in_order_id}/status", response_model=DineInOrderOut)
async def update_dine_in_order_status(
    dine_in_order_id: int,
    payload: DineInOrderStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """
    店員標記訂單已完成／已出餐，只有 role="staff" 能呼叫。標記為 completed 時，
    順便依訂單實付金額（total_amount，已扣點數折抵）發放消費回饋點數——用
    order.status 是否已經是 completed 判斷，避免同一張單重複點擊而重複發點。
    """
    query = (
        select(DineInOrder)
        .where(DineInOrder.id == dine_in_order_id)
        .options(DINE_IN_ORDER_LOAD_OPTIONS)
    )
    result = await db.execute(query)
    order = result.scalars().first()
    if not order:
        raise HTTPException(status_code=404, detail="訂單不存在")

    try:
        newly_completed = payload.status == "completed" and order.status != "completed"
        order.status = payload.status
        if newly_completed:
            earned = loyalty_service.calc_earned_points(order.total_amount)
            await loyalty_service.earn_points(
                db,
                order.user_id,
                earned,
                reason=f"消費回饋：堂食訂單 #{order.id}",
                related_dine_in_order_id=order.id,
            )
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 更新堂食訂單狀態失敗: {e}")
        raise HTTPException(status_code=500, detail="更新訂單狀態失敗")

    query = (
        select(DineInOrder)
        .where(DineInOrder.id == dine_in_order_id)
        .options(DINE_IN_ORDER_LOAD_OPTIONS)
    )
    result = await db.execute(query)
    return _to_order_out(result.scalars().first())
