# app/api/v1/endpoints/tables.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import Table
from app.schemas.table import TableCreate, TableOut

router = APIRouter()

# 三支端點都要求 role="staff"（deps.get_current_staff_user），顧客帳號呼叫會 403，
# 且一律依登入店員的 User.restaurant_id 自動 scope，不接受前端傳門市參數
# （2026-09 多門市支援：店員一人只屬於一間門市，畫面上只需要唯讀顯示「目前門市」，
# 不需要門市選擇器，見 CLAUDE.md 多門市一節）。


@router.get("", response_model=List[TableOut])
async def list_tables(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """列出目前登入店員所屬門市的桌位，供店員管理畫面使用。"""
    if current_user.restaurant_id is None:
        raise HTTPException(status_code=400, detail="帳號尚未指定所屬門市")
    query = (
        select(Table)
        .where(Table.restaurant_id == current_user.restaurant_id)
        .order_by(Table.code)
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=TableOut, status_code=201)
async def create_table(
    payload: TableCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """新增桌位到目前登入店員所屬門市。桌號在同一門市內重複回 409。"""
    if current_user.restaurant_id is None:
        raise HTTPException(status_code=400, detail="帳號尚未指定所屬門市")
    try:
        table = Table(code=payload.code, restaurant_id=current_user.restaurant_id)
        db.add(table)
        await db.flush()
        table_id = table.id
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"桌號 {payload.code} 已存在")
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 新增桌位失敗: {e}")
        raise HTTPException(status_code=500, detail="新增桌位失敗")

    result = await db.execute(select(Table).where(Table.id == table_id))
    return result.scalars().first()


@router.delete("/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_table(
    table_id: int,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """刪除桌位（只能刪除自己門市的桌位），找不到回 404。"""
    if current_user.restaurant_id is None:
        raise HTTPException(status_code=400, detail="帳號尚未指定所屬門市")

    result = await db.execute(
        select(Table.id).where(
            Table.id == table_id,
            Table.restaurant_id == current_user.restaurant_id,
        )
    )
    if result.first() is None:
        raise HTTPException(status_code=404, detail="桌位不存在")

    try:
        await db.execute(delete(Table).where(Table.id == table_id))
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 刪除桌位失敗: {e}")
        raise HTTPException(status_code=500, detail="刪除桌位失敗")
