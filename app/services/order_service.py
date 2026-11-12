"""
網購訂單的共用邏輯（2026-11 補金流缺口，詳見 docs/ecpay-checkout.md「付款失敗與逾時」）。

`release_order_holds` 把建立訂單當下就先佔用的三樣東西退回去：商品庫存、優惠券、
折抵的點數。三個地方共用：
- 使用者主動取消（POST /orders/{id}/cancel）
- ECPay 回報付款失敗（POST /orders/ecpay/callback，RtnCode≠1）
- 待付款超過 30 分鐘自動取消（scheduler_service 的排程）

呼叫端負責：先用 SELECT ... FOR UPDATE 鎖住訂單列並確認 status 還是 "pending"
（避免兩邊同時處理而退兩次）、order.items 要已載入、改 status、commit。
這裡不 commit。
"""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Order
from app.models import Product as ProductModel
from app.services import coupon_service, loyalty_service

PENDING_TIMEOUT = timedelta(minutes=30)


async def release_order_holds(db: AsyncSession, order: Order, reason: str) -> None:
    """退回庫存／優惠券／點數（不 commit）。reason 寫進 reverse_redeem 點數明細。"""
    for item in order.items:
        await db.execute(
            update(ProductModel)
            .where(ProductModel.id == item.product_id)
            .values(stock=ProductModel.stock + item.quantity)
        )

    if order.coupon_id is not None:
        await coupon_service.release_coupon(db, order.coupon_id)

    if order.points_used > 0:
        await loyalty_service.reverse_redeem_points(
            db,
            order.user_id,
            order.points_used,
            reason=reason,
            related_order_id=order.id,
        )


async def lock_order(db: AsyncSession, order_id: int) -> Order | None:
    """鎖住訂單列（FOR UPDATE）並載入 items，給要改狀態、退回佔用的流程用。"""
    return (
        await db.execute(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
            .with_for_update(of=Order)
        )
    ).scalars().first()


async def cancel_expired_pending_orders(db_factory) -> dict:
    """
    把建立超過 30 分鐘還是 pending 的網購訂單改成 cancelled 並退回佔用。
    每筆訂單各自一個 transaction：先鎖住、再確認仍是 pending（ECPay 付款成功的
    callback 可能剛好搶先一步），單筆失敗不影響其他筆。回傳統計，例外往外丟給呼叫端回報。
    """
    cutoff = datetime.now(UTC) - PENDING_TIMEOUT
    async with db_factory() as db:
        order_ids = (
            await db.execute(
                select(Order.id)
                .where(Order.status == "pending", Order.created_at < cutoff)
                .order_by(Order.id)
            )
        ).scalars().all()
        await db.commit()

    cancelled, skipped, errors = 0, 0, []
    for order_id in order_ids:
        async with db_factory() as db:
            try:
                order = await lock_order(db, order_id)
                if order is None or order.status != "pending":
                    skipped += 1
                    await db.rollback()
                    continue
                await release_order_holds(
                    db, order, reason=f"訂單逾時未付款自動取消退還：訂單 #{order_id}"
                )
                order.status = "cancelled"
                await db.commit()
                cancelled += 1
            except Exception as e:
                await db.rollback()
                errors.append((order_id, e))
    return {"found": len(order_ids), "cancelled": cancelled, "skipped": skipped, "errors": errors}
