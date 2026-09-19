# app/api/v1/endpoints/friends.py
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.database_async import AsyncSessionLocal, get_db
from app.models import Friendship, User
from app.schemas.friend import (
    FriendOut,
    FriendRequestCreate,
    FriendRequestOut,
    FriendshipOut,
    GreetingCreate,
)
from app.services.push_service import send_user_push_notifications

router = APIRouter()

FRIEND_LOAD_OPTIONS = (
    selectinload(Friendship.user_a),
    selectinload(Friendship.user_b),
)


def display_name(user: User) -> str:
    # 暱稱優先；沒填才退回用 email 前綴遮罩後（前 2 碼 + ***）當顯示名稱，
    # 純 LINE 帳號沒有 email 就用「用戶{id}」。API 回應欄位仍叫 username，
    # 推播文案也共用這個函式
    if user.nickname:
        return user.nickname
    if user.email:
        return f"{user.email.split('@')[0][:2]}***"
    return f"用戶{user.id}"


def _other(friendship: Friendship, me_id: int) -> User:
    return friendship.user_b if friendship.user_a_id == me_id else friendship.user_a


def _to_friendship_out(friendship: Friendship, me_id: int) -> FriendshipOut:
    other = _other(friendship, me_id)
    return FriendshipOut(
        id=friendship.id,
        status=friendship.status,
        requester_id=friendship.requester_id,
        user_id=other.id,
        username=display_name(other),
        avatar_url=other.avatar_url,
    )


async def _load_friendship(db: AsyncSession, friendship_id: int) -> Friendship | None:
    result = await db.execute(
        select(Friendship)
        .where(Friendship.id == friendship_id)
        .options(*FRIEND_LOAD_OPTIONS)
        .execution_options(populate_existing=True)
    )
    return result.scalars().first()


def _push(
    background_tasks: BackgroundTasks,
    user_id: int,
    title: str,
    body: str,
    data: dict,
    image_url: str | None = None,
):
    # 比照 push_service 既有慣例：session 已經 commit 完才觸發，不佔用交易時間
    background_tasks.add_task(
        send_user_push_notifications,
        AsyncSessionLocal,
        user_id,
        "mynotification",
        title,
        body,
        data,
        image_url,
    )


@router.get("", response_model=List[FriendOut])
async def list_friends(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """已接受的好友列表，新加入的在前。"""
    me = current_user.id
    result = await db.execute(
        select(Friendship)
        .where(
            Friendship.status == "accepted",
            or_(Friendship.user_a_id == me, Friendship.user_b_id == me),
        )
        .options(*FRIEND_LOAD_OPTIONS)
        .order_by(Friendship.responded_at.desc())
    )
    friends = []
    for friendship in result.scalars().all():
        other = _other(friendship, me)
        friends.append(
            FriendOut(
                friendship_id=friendship.id,
                user_id=other.id,
                username=display_name(other),
                avatar_url=other.avatar_url,
                since=friendship.responded_at,
            )
        )
    return friends


@router.get("/requests", response_model=List[FriendRequestOut])
async def list_friend_requests(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """收到的待確認邀請（別人發給我、狀態 pending），新到舊。"""
    me = current_user.id
    result = await db.execute(
        select(Friendship)
        .where(
            Friendship.status == "pending",
            Friendship.requester_id != me,
            or_(Friendship.user_a_id == me, Friendship.user_b_id == me),
        )
        .options(*FRIEND_LOAD_OPTIONS)
        .order_by(Friendship.created_at.desc())
    )
    requests = []
    for friendship in result.scalars().all():
        requester = _other(friendship, me)
        requests.append(
            FriendRequestOut(
                id=friendship.id,
                requester_id=requester.id,
                username=display_name(requester),
                avatar_url=requester.avatar_url,
                created_at=friendship.created_at,
            )
        )
    return requests


@router.post("/requests", response_model=FriendshipOut, status_code=201)
async def create_friend_request(
    payload: FriendRequestCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    送出好友邀請（掃對方 QR Code 後呼叫）。同一對使用者只會有一列 Friendship
    （user_a_id/user_b_id 正規化 + UniqueConstraint），所以：
    - 沒有現存列 → 建立 pending 並推播對方（friend_request）
    - 對方先前已邀請我、還在 pending → 視為互相同意，直接改成 accepted 並推播
      對方（friend_request_accepted）；雙方同時互掃造成的 race 也收斂到這條路
    - 已是好友 → 409 + error_code "already_friends"（比照點數錯誤的物件格式）
    - 我已邀請過還在 pending → idempotent，回傳現況，不重複推播
    - 先前被拒絕（declined）→ 同一列改回 pending、requester 換成我，重新推播
    commit 後不再碰 ORM 物件屬性（避免 MissingGreenlet），需要的值先存區域變數。
    """
    me = current_user.id
    my_name = display_name(current_user)
    my_avatar = current_user.avatar_url  # 推播帶送出者頭貼，commit 前先存起來
    target_id = payload.target_user_id
    if target_id == me:
        raise HTTPException(status_code=400, detail="不能加自己為好友")
    if await db.get(User, target_id) is None:
        raise HTTPException(status_code=404, detail="使用者不存在")

    a_id, b_id = sorted((me, target_id))
    pair_filter = (Friendship.user_a_id == a_id, Friendship.user_b_id == b_id)
    notify = None  # (接收者 user_id, type)

    try:
        row = (
            await db.execute(select(Friendship).where(*pair_filter))
        ).scalars().first()
        friendship_id = None
        if row is None:
            new_row = Friendship(
                user_a_id=a_id, user_b_id=b_id, requester_id=me, status="pending"
            )
            db.add(new_row)
            try:
                await db.flush()
                friendship_id = new_row.id
                await db.commit()
                notify = (target_id, "friend_request")
            except IntegrityError:
                # 同時間對方也剛好建立了同一對的列：改走下面「現存列」的判斷
                await db.rollback()
                row = (
                    await db.execute(select(Friendship).where(*pair_filter))
                ).scalars().first()
        if friendship_id is None:
            friendship_id, row_status, row_requester = row.id, row.status, row.requester_id
            if row_status == "accepted":
                raise HTTPException(
                    status_code=409,
                    detail={"error_code": "already_friends", "message": "已經是好友"},
                )
            if row_status == "pending" and row_requester != me:
                updated = await db.execute(
                    update(Friendship)
                    .where(Friendship.id == friendship_id, Friendship.status == "pending")
                    .values(status="accepted", responded_at=func.now())
                    .returning(Friendship.id)
                )
                if updated.first() is not None:
                    notify = (row_requester, "friend_request_accepted")
            elif row_status == "declined":
                updated = await db.execute(
                    update(Friendship)
                    .where(Friendship.id == friendship_id, Friendship.status == "declined")
                    .values(status="pending", requester_id=me, responded_at=None)
                    .returning(Friendship.id)
                )
                if updated.first() is not None:
                    notify = (target_id, "friend_request")
            await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 建立好友邀請失敗: {e}")
        raise HTTPException(status_code=500, detail="建立好友邀請失敗")

    if notify is not None:
        recipient_id, notify_type = notify
        if notify_type == "friend_request":
            title, body = "👋 新的好友邀請", f"{my_name} 想加你為好友"
        else:
            title, body = "🎉 好友邀請已接受", f"{my_name} 接受了你的好友邀請"
        _push(
            background_tasks,
            recipient_id,
            title,
            body,
            {"type": notify_type, "screen": "Friends", "friendship_id": friendship_id},
            my_avatar,
        )

    return _to_friendship_out(await _load_friendship(db, friendship_id), me)


async def _respond_to_request(
    friendship_id: int,
    new_status: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
    current_user,
) -> FriendshipOut:
    me = current_user.id
    my_name = display_name(current_user)
    my_avatar = current_user.avatar_url
    row = await _load_friendship(db, friendship_id)
    # 不是這對關係的成員、或邀請是我自己發的（不能自己接受自己的邀請）一律當作不存在
    if row is None or me not in (row.user_a_id, row.user_b_id) or row.requester_id == me:
        raise HTTPException(status_code=404, detail="邀請不存在")
    requester_id = row.requester_id

    try:
        updated = await db.execute(
            update(Friendship)
            .where(Friendship.id == friendship_id, Friendship.status == "pending")
            .values(status=new_status, responded_at=func.now())
            .returning(Friendship.id)
        )
        changed = updated.first() is not None
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 回應好友邀請失敗: {e}")
        raise HTTPException(status_code=500, detail="回應好友邀請失敗")

    result = await _load_friendship(db, friendship_id)
    if not changed and result.status != new_status:
        raise HTTPException(status_code=409, detail="這個邀請已經不是待確認狀態")

    if changed and new_status == "accepted":
        _push(
            background_tasks,
            requester_id,
            "🎉 好友邀請已接受",
            f"{my_name} 接受了你的好友邀請",
            {
                "type": "friend_request_accepted",
                "screen": "Friends",
                "friendship_id": friendship_id,
            },
            my_avatar,
        )
    return _to_friendship_out(result, me)


@router.post("/requests/{friendship_id}/accept", response_model=FriendshipOut)
async def accept_friend_request(
    friendship_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """接受邀請（只有收到邀請的人能呼叫）；已接受過再呼叫是 idempotent，已拒絕回 409。"""
    return await _respond_to_request(
        friendship_id, "accepted", background_tasks, db, current_user
    )


@router.post("/requests/{friendship_id}/reject", response_model=FriendshipOut)
async def reject_friend_request(
    friendship_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """拒絕邀請（不推播對方）；已拒絕過再呼叫是 idempotent，已接受回 409。"""
    return await _respond_to_request(
        friendship_id, "declined", background_tasks, db, current_user
    )


@router.post("/{friendship_id}/greetings")
async def send_greeting(
    friendship_id: int,
    payload: GreetingCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    對好友送出快速訊息（前端固定選項字串）：只推播對方，推播本身會由
    send_user_push_notifications 寫進對方的 NotificationLog，不另外建訊息表。
    只有已接受的好友關係才能送（不是成員回 404、還不是好友回 409）。
    """
    me = current_user.id
    my_name = display_name(current_user)
    my_avatar = current_user.avatar_url
    row = await _load_friendship(db, friendship_id)
    if row is None or me not in (row.user_a_id, row.user_b_id):
        raise HTTPException(status_code=404, detail="好友關係不存在")
    if row.status != "accepted":
        raise HTTPException(status_code=409, detail="還不是好友")
    recipient_id = row.user_b_id if row.user_a_id == me else row.user_a_id

    _push(
        background_tasks,
        recipient_id,
        f"💬 {my_name}",
        payload.message,
        {"type": "friend_greeting", "screen": "Friends", "friendship_id": friendship_id},
        my_avatar,
    )
    return {"ok": True}
