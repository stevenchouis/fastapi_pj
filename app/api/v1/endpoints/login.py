import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.core import security  # 假設你這裡有 create_access_token 邏輯
from app.core.config import settings
from app.database_async import get_db
from app.services.email_service import send_magic_link_email

# from app.main4 import create_access_token

router = APIRouter()


@router.post("/login/access-token")
async def login_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),  # 改用 AsyncSession 型別
):
    # 1. 使用 where 並確保 models 匯入正確
    statement = select(models.User).where(models.User.email == form_data.username)
    print(f"DEBUG DB TYPE: {type(db)}")
    # 2. 執行並捕捉結果
    result = await db.execute(statement)

    # 3. 使用 scalar_one_or_none 防止多筆或無資料時報錯
    # user = result.scalar_one_or_none()
    user = result.scalars().first()

    if not user or not security.verify_password(
        form_data.password, user.hashed_password
    ):
        raise HTTPException(status_code=400, detail="帳號或密碼錯誤")

    # 3. 檢查帳號是否啟用
    if not user.is_active:
        raise HTTPException(status_code=400, detail="帳號已被停用")

    # 產生一個代表 30 分鐘長度的 timedelta 物件
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        # subject=user.email,  # 這裡對應函式的 subject 參數
        subject=str(user.id),  # 將 ID 轉為字串放入 sub
        expires_delta=access_token_expires,
    )
    # access_token = security.create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/login/google")
async def login_google(
    payload: schemas.GoogleLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    # 1. 驗證 Google id_token 的簽章、有效期限與 audience（離線驗證，不額外發 HTTP request）
    try:
        idinfo = google_id_token.verify_oauth2_token(
            payload.id_token, google_requests.Request(), settings.GOOGLE_CLIENT_ID
        )
    except ValueError as e:
        print(f"DEBUG: Google id_token 驗證失敗: {e}")
        raise HTTPException(status_code=400, detail="Google 登入驗證失敗")

    # 只信任 Google 已驗證過的 Email，避免自動綁定時發生帳號接管風險
    if not idinfo.get("email_verified"):
        raise HTTPException(status_code=400, detail="Google 帳號的 Email 尚未驗證")

    google_id = idinfo["sub"]
    email = idinfo["email"]

    # 2. 先用 google_id 找出是否已經綁定過的帳號
    statement = select(models.User).where(models.User.google_id == google_id)
    result = await db.execute(statement)
    user = result.scalars().first()

    if not user:
        # 3. 尚未綁定過，改用 email 查找既有帳號（原本可能是用密碼註冊）
        statement = select(models.User).where(models.User.email == email)
        result = await db.execute(statement)
        user = result.scalars().first()

        try:
            if user:
                # 既有帳號自動補綁定 google_id，視為同一使用者
                user.google_id = google_id
                user.auth_provider = "both"
            else:
                # 全新使用者，純 Google 帳號沒有密碼
                user = models.User(
                    email=email,
                    google_id=google_id,
                    hashed_password=None,
                    auth_provider="google",
                    is_active=True,
                )
                db.add(user)
            await db.commit()
            await db.refresh(user)
        except Exception as e:
            await db.rollback()
            print(f"DEBUG: Google 登入建立/綁定使用者失敗: {e}")
            raise HTTPException(status_code=500, detail="Google 登入處理失敗")

    if not user.is_active:
        raise HTTPException(status_code=400, detail="帳號已被停用")

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        subject=str(user.id),
        expires_delta=access_token_expires,
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/login/line/redirect")
async def line_login_redirect(request: Request):
    """
    LINE 授權完成後的中繼落地頁。LINE Console 的 Callback URL 只接受 https 網址，
    不能直接填 App 的 mynotification:// 自訂 scheme，所以比照 Magic Link 登入的做法：
    先落地在這支後端網址，再把 LINE 帶來的 query params（code/state，或使用者取消時的
    error/error_description）原封不動轉跳到 App 的自訂 scheme，交給 App 端處理。
    """
    query_string = urlencode(dict(request.query_params))
    deep_link = "mynotification://redirect"
    if query_string:
        deep_link += f"?{query_string}"
    return RedirectResponse(url=deep_link, status_code=302)


@router.post("/login/line")
async def login_line(
    payload: schemas.LineLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    # LINE Console 上註冊的 Callback URL 是固定值（就是上面這支 /login/line/redirect），
    # 跟 LINE 換 token 時的 redirect_uri 必須跟註冊值一字不差，所以這裡自己組，不吃前端傳來的值
    redirect_uri = f"{settings.BASE_URL}{settings.API_V1_STR}/login/line/redirect"

    # 1. 拿授權碼跟 LINE 換 token（一併換到 id_token）
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://api.line.me/oauth2/v2.1/token",
            data={
                "grant_type": "authorization_code",
                "code": payload.code,
                "redirect_uri": redirect_uri,
                "client_id": settings.LINE_CHANNEL_ID,
                "client_secret": settings.LINE_CHANNEL_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_response.status_code != 200:
            print(f"DEBUG: LINE token 交換失敗: {token_response.text}")
            raise HTTPException(status_code=400, detail="LINE 登入驗證失敗")
        id_token = token_response.json().get("id_token")
        if not id_token:
            raise HTTPException(status_code=400, detail="LINE 登入驗證失敗")

        # 2. 交給 LINE 的 verify 端點驗證 id_token 簽章與 audience，換回解碼後的 claims
        verify_response = await client.post(
            "https://api.line.me/oauth2/v2.1/verify",
            data={"id_token": id_token, "client_id": settings.LINE_CHANNEL_ID},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if verify_response.status_code != 200:
            print(f"DEBUG: LINE id_token 驗證失敗: {verify_response.text}")
            raise HTTPException(status_code=400, detail="LINE 登入驗證失敗")
        claims = verify_response.json()

    line_id = claims["sub"]
    # LINE 預設不給 Email（需另外申請權限），所以這裡可能是 None
    email = claims.get("email")

    # 3. 先用 line_id 找出是否已經綁定過的帳號
    statement = select(models.User).where(models.User.line_id == line_id)
    result = await db.execute(statement)
    user = result.scalars().first()

    if not user:
        # 4. 尚未綁定過；如果 LINE 有給 email，改用 email 查找既有帳號並自動補綁定
        if email:
            statement = select(models.User).where(models.User.email == email)
            result = await db.execute(statement)
            user = result.scalars().first()

        try:
            if user:
                user.line_id = line_id
                user.auth_provider = "both"
            else:
                # 全新使用者，純 LINE 帳號沒有密碼；email 可能是 None
                user = models.User(
                    email=email,
                    line_id=line_id,
                    hashed_password=None,
                    auth_provider="line",
                    is_active=True,
                )
                db.add(user)
            await db.commit()
            await db.refresh(user)
        except Exception as e:
            await db.rollback()
            print(f"DEBUG: LINE 登入建立/綁定使用者失敗: {e}")
            raise HTTPException(status_code=500, detail="LINE 登入處理失敗")

    if not user.is_active:
        raise HTTPException(status_code=400, detail="帳號已被停用")

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        subject=str(user.id),
        expires_delta=access_token_expires,
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/login/magic-link/request", status_code=status.HTTP_204_NO_CONTENT)
async def request_magic_link(
    payload: schemas.MagicLinkRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    email = payload.email.lower()

    # 60 秒內若已寄過信，就不再重寄，避免被拿來對別人的信箱灌信騷擾
    cooldown_since = datetime.now(UTC) - timedelta(seconds=60)
    statement = (
        select(models.MagicLinkToken)
        .where(models.MagicLinkToken.email == email)
        .where(models.MagicLinkToken.created_at > cooldown_since)
        .order_by(models.MagicLinkToken.created_at.desc())
    )
    result = await db.execute(statement)
    recent_token = result.scalars().first()

    if not recent_token:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.now(UTC) + timedelta(minutes=15)

        try:
            db.add(
                models.MagicLinkToken(
                    email=email, token_hash=token_hash, expires_at=expires_at
                )
            )
            await db.commit()
        except Exception as e:
            await db.rollback()
            print(f"DEBUG: Magic Link token 建立失敗: {e}")
            # 失敗也不讓前端知道細節，一律回傳相同結果，避免帳號枚舉／內部錯誤外洩
            return

        link = (
            f"{settings.BASE_URL}{settings.API_V1_STR}"
            f"/login/magic-link/redirect?token={raw_token}"
        )
        background_tasks.add_task(send_magic_link_email, email, link)

    # 無論 email 是否存在、是否觸發 cooldown，一律回傳相同結果，避免帳號枚舉
    return


@router.get("/login/magic-link/redirect", response_class=HTMLResponse)
async def magic_link_redirect(token: str):
    """信件連結的落地頁。這裡只單純轉跳，不會消耗 token —— 因為部分信箱服務
    （如 Outlook/Gmail 的安全掃描）會自動預先擷取信件中的連結，若在這一步就把
    token 標記為已使用，使用者自己點擊時 token 早就被機器人用掉了。
    真正消耗 token 的動作在 /login/magic-link/verify（由 App 呼叫）。"""
    deep_link = f"mynotification://magic-login?token={token}"
    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<meta http-equiv="refresh" content="0;url={deep_link}" />
<script>window.location.replace("{deep_link}");</script>
</head>
<body>
<p>正在開啟 App…如果沒有自動跳轉，請點擊<a href="{deep_link}">這裡</a>。</p>
</body>
</html>"""
    return HTMLResponse(content=html_content)


@router.post("/login/magic-link/verify")
async def verify_magic_link(
    payload: schemas.MagicLinkVerify,
    db: AsyncSession = Depends(get_db),
):
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    now = datetime.now(UTC)

    # 用一次 UPDATE ... WHERE used_at IS NULL 原子性地標記為已使用，
    # 避免同一個 token 被同時打兩次 verify 時重複發放登入
    statement = (
        update(models.MagicLinkToken)
        .where(models.MagicLinkToken.token_hash == token_hash)
        .where(models.MagicLinkToken.used_at.is_(None))
        .where(models.MagicLinkToken.expires_at > now)
        .values(used_at=now)
        .returning(models.MagicLinkToken.email)
    )
    result = await db.execute(statement)
    row = result.first()
    if row is None:
        await db.rollback()
        raise HTTPException(status_code=400, detail="登入連結無效或已過期")

    email = row[0]

    statement = select(models.User).where(models.User.email == email)
    result = await db.execute(statement)
    user = result.scalars().first()

    try:
        if not user:
            # 全新使用者，純 Magic Link 帳號沒有密碼
            user = models.User(
                email=email,
                hashed_password=None,
                auth_provider="magic_link",
                is_active=True,
            )
            db.add(user)
        await db.commit()
        await db.refresh(user)
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: Magic Link 登入建立使用者失敗: {e}")
        raise HTTPException(status_code=500, detail="登入處理失敗")

    if not user.is_active:
        raise HTTPException(status_code=400, detail="帳號已被停用")

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        subject=str(user.id),
        expires_delta=access_token_expires,
    )
    return {"access_token": access_token, "token_type": "bearer"}
