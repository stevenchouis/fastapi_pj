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

# 三支端點都要求 role="staff"（deps.get_current_staff_user），顧客帳號呼叫會 403。


@router.get("", response_model=List[TableOut])
async def list_tables(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """列出所有桌位，供店員管理畫面使用。"""
    query = select(Table).order_by(Table.code)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("", response_model=TableOut, status_code=201)
async def create_table(
    payload: TableCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_staff_user),
):
    """新增桌位。桌號重複回 409。"""
    try:
        table = Table(code=payload.code)
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
    """刪除桌位，找不到回 404。"""
    result = await db.execute(select(Table.id).where(Table.id == table_id))
    if result.first() is None:
        raise HTTPException(status_code=404, detail="桌位不存在")

    try:
        await db.execute(delete(Table).where(Table.id == table_id))
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 刪除桌位失敗: {e}")
        raise HTTPException(status_code=500, detail="刪除桌位失敗")
