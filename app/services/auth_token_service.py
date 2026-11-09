"""
2026-11 登入自動續期（refresh token）。

- 通行證（access token）：JWT，效期 settings.ACCESS_TOKEN_EXPIRE_MINUTES（customer／staff 相同）
- 續期證（refresh token）：secrets.token_urlsafe(48) 隨機字串，DB 只存 SHA-256 雜湊；
  效期依角色（customer 30 天、staff 7 天），每次換證重新起算
- 換證採 rotation：舊的標記 rotated_at、發新的。同一張在換證後 30 秒內又被拿來用
  （網路重送／兩個請求同時送出）會再發一組新的（舊的那組仍有效），不算偷用；
  超過 30 秒視為偷用，作廢該使用者全部續期證
"""
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.core import security
from app.core.config import settings

REFRESH_TTL_BY_ROLE = {"customer": timedelta(days=30), "staff": timedelta(days=7)}
DEFAULT_REFRESH_TTL = timedelta(days=30)
REUSE_GRACE = timedelta(seconds=30)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _short_token_user_ids() -> set[int]:
    ids = set()
    for part in settings.SHORT_ACCESS_TOKEN_USER_IDS.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def create_access_token_for(user_id: int) -> str:
    """通行證效期維持 ACCESS_TOKEN_EXPIRE_MINUTES；列在 SHORT_ACCESS_TOKEN_USER_IDS 的
    測試帳號改用 SHORT_ACCESS_TOKEN_MINUTES，方便實機測「過期後自動續期」。"""
    minutes = settings.ACCESS_TOKEN_EXPIRE_MINUTES
    if user_id in _short_token_user_ids():
        minutes = settings.SHORT_ACCESS_TOKEN_MINUTES
    return security.create_access_token(
        subject=str(user_id), expires_delta=timedelta(minutes=minutes)
    )


def invalid_refresh_token() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"error_code": "invalid_refresh_token", "message": "登入已過期，請重新登入"},
    )


async def _add_refresh_token(db: AsyncSession, user_id: int, role: str) -> str:
    """新增一張續期證（不 commit）。順手清掉這個使用者已過期的舊列，避免資料表一直長大。"""
    now = datetime.now(UTC)
    await db.execute(
        delete(models.RefreshToken).where(
            models.RefreshToken.user_id == user_id, models.RefreshToken.expires_at <= now
        )
    )
    raw = secrets.token_urlsafe(48)
    db.add(
        models.RefreshToken(
            user_id=user_id,
            token_hash=_hash(raw),
            role=role,
            expires_at=now + REFRESH_TTL_BY_ROLE.get(role, DEFAULT_REFRESH_TTL),
        )
    )
    return raw


async def revoke_all_for_user(db: AsyncSession, user_id: int) -> None:
    """作廢該使用者全部續期證（不 commit）。"""
    await db.execute(
        update(models.RefreshToken)
        .where(models.RefreshToken.user_id == user_id, models.RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def login_response(db: AsyncSession, user: models.User) -> dict:
    """四個登入端點共用：發通行證＋一張新的續期證並 commit。"""
    user_id, role = user.id, user.role or "customer"
    try:
        raw = await _add_refresh_token(db, user_id, role)
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 建立續期證失敗: {e}")
        raise HTTPException(status_code=500, detail="登入處理失敗")
    return {
        "access_token": create_access_token_for(user_id),
        "token_type": "bearer",
        "refresh_token": raw,
    }


async def _active_user_role(db: AsyncSession, user_id: int) -> str | None:
    row = (
        await db.execute(
            select(models.User.is_active, models.User.role).where(models.User.id == user_id)
        )
    ).first()
    if row is None or not row.is_active:
        return None
    return row.role or "customer"


async def refresh(db: AsyncSession, raw: str) -> dict:
    now = datetime.now(UTC)
    token_hash = _hash(raw)

    # 原子性地把這張標記成已換證；兩個請求同時送同一張只有一個會成功，另一個走下面的寬限判斷
    row = (
        await db.execute(
            update(models.RefreshToken)
            .where(
                models.RefreshToken.token_hash == token_hash,
                models.RefreshToken.rotated_at.is_(None),
                models.RefreshToken.revoked_at.is_(None),
                models.RefreshToken.expires_at > now,
            )
            .values(rotated_at=now)
            .returning(models.RefreshToken.user_id, models.RefreshToken.role)
        )
    ).first()

    if row is None:
        existing = (
            await db.execute(
                select(models.RefreshToken).where(models.RefreshToken.token_hash == token_hash)
            )
        ).scalars().first()
        if (
            existing is None
            or existing.revoked_at is not None
            or existing.expires_at <= now
            or existing.rotated_at is None
        ):
            await db.rollback()
            raise invalid_refresh_token()
        user_id, token_role = existing.user_id, existing.role
        if now - existing.rotated_at > REUSE_GRACE:
            # 已換過證的舊續期證超過寬限又被拿來用：視為被偷用，全部作廢
            try:
                await revoke_all_for_user(db, user_id)
                await db.commit()
            except Exception as e:
                await db.rollback()
                print(f"DEBUG: 偷用偵測作廢續期證失敗: {e}")
            raise invalid_refresh_token()
        # 寬限內（網路重送）：往下照常再發一組新的
    else:
        user_id, token_role = row.user_id, row.role

    # 帳號已刪除／停用，或角色跟發證當下不同（直接改 DB 變更角色）：作廢全部
    current_role = await _active_user_role(db, user_id)
    if current_role is None or current_role != token_role:
        try:
            await revoke_all_for_user(db, user_id)
            await db.commit()
        except Exception as e:
            await db.rollback()
            print(f"DEBUG: 作廢續期證失敗: {e}")
        raise invalid_refresh_token()

    try:
        new_raw = await _add_refresh_token(db, user_id, current_role)
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 換發續期證失敗: {e}")
        raise HTTPException(status_code=500, detail="換證失敗")
    return {
        "access_token": create_access_token_for(user_id),
        "token_type": "bearer",
        "refresh_token": new_raw,
    }


async def logout(db: AsyncSession, raw: str) -> None:
    try:
        await db.execute(
            update(models.RefreshToken)
            .where(
                models.RefreshToken.token_hash == _hash(raw),
                models.RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 登出作廢續期證失敗: {e}")
