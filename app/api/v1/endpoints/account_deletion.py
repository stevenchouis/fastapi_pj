"""
2026-11 網頁刪除帳號申請（Google Play 資料安全性表單要求「不安裝 App 也能申請刪除帳號」的網址），
詳見 docs/account-deletion-web.md。

流程比照 Magic Link：輸入 Email → 寄確認信（30 分鐘、單次使用）→ 點連結開確認頁 → 再按一次
「確認刪除帳號」才真的刪（避免信箱安全掃描機器人預先打開連結就把帳號刪掉）。刪除邏輯跟
App 內的 DELETE /users/me 共用 account_service.delete_user_account。

- page_router：給人看的 HTML 頁面，掛在根路徑（/account-deletion、/account-deletion/confirm）
- router：頁面呼叫的 API，掛在 /api/v1/account-deletion
"""
import hashlib
import html
import json
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.core.config import settings
from app.database_async import get_db
from app.schemas.account_deletion import AccountDeletionConfirm, AccountDeletionRequest
from app.services import account_service
from app.services.email_service import send_account_deletion_email

router = APIRouter()
page_router = APIRouter()

TOKEN_TTL = timedelta(minutes=30)
PER_MINUTE_LIMIT = 1
PER_DAY_LIMIT = 5


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def mask_email(email: str) -> str:
    """st***@hotmail.com：只露出帳號前 2 碼與網域。"""
    local, _, domain = email.partition("@")
    return f"{local[:2]}***@{domain}" if domain else f"{local[:2]}***"


async def _active_user_by_email(db: AsyncSession, email: str) -> models.User | None:
    return (
        await db.execute(
            select(models.User).where(
                func.lower(models.User.email) == email.lower(), models.User.is_active.is_(True)
            )
        )
    ).scalars().first()


async def _valid_token_row(db: AsyncSession, raw: str) -> models.AccountDeletionToken | None:
    return (
        await db.execute(
            select(models.AccountDeletionToken).where(
                models.AccountDeletionToken.token_hash == _hash(raw),
                models.AccountDeletionToken.used_at.is_(None),
                models.AccountDeletionToken.expires_at > datetime.now(UTC),
            )
        )
    ).scalars().first()


def _invalid_token() -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error_code": "invalid_deletion_token", "message": "連結已失效，請重新申請"},
    )


def _staff_forbidden() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"error_code": "staff_account", "message": "店員帳號無法自行刪除，請聯絡管理者"},
    )


# ---------------------------------------------------------------- API


@router.post("/request", status_code=status.HTTP_204_NO_CONTENT)
async def request_account_deletion(
    payload: AccountDeletionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """一律回 204，不透露這個 Email 有沒有註冊、有沒有寄信。
    只有 Email 對應到啟用中的帳號才建立 token 並寄信；同一 Email 每分鐘 1 次、每天 5 次，超過不寄。"""
    email = payload.email.lower()
    now = datetime.now(UTC)
    try:
        # 同一個 Email 的申請排隊處理，避免兩個請求同時通過「每分鐘 1 次」的檢查
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"acct-del:{email}"})
        recent = (
            await db.execute(
                select(
                    func.count().filter(models.AccountDeletionToken.created_at > now - timedelta(minutes=1)),
                    func.count(),
                ).where(
                    models.AccountDeletionToken.email == email,
                    models.AccountDeletionToken.created_at > now - timedelta(days=1),
                )
            )
        ).first()
        if recent[0] >= PER_MINUTE_LIMIT or recent[1] >= PER_DAY_LIMIT:
            await db.rollback()
            return

        user = await _active_user_by_email(db, email)
        if user is None:
            await db.rollback()
            return

        raw = secrets.token_urlsafe(32)
        db.add(models.AccountDeletionToken(email=email, token_hash=_hash(raw), expires_at=now + TOKEN_TTL))
        await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 建立刪除帳號申請失敗: {e}")
        return

    link = f"{settings.BASE_URL}/account-deletion/confirm?token={raw}"
    background_tasks.add_task(send_account_deletion_email, email, link)


@router.post("/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_account_deletion(
    payload: AccountDeletionConfirm,
    db: AsyncSession = Depends(get_db),
):
    """token 有效才刪除。店員帳號 403（不消耗 token）；token 無效／過期／已用過 400。"""
    row = await _valid_token_row(db, payload.token)
    if row is None:
        raise _invalid_token()
    token_id, email = row.id, row.email
    user = await _active_user_by_email(db, email)
    if user is None:
        raise _invalid_token()
    if user.role == "staff":
        raise _staff_forbidden()

    try:
        # 原子性地標記已使用，同一個 token 同時送兩次只有一個會成功
        used = (
            await db.execute(
                update(models.AccountDeletionToken)
                .where(
                    models.AccountDeletionToken.id == token_id,
                    models.AccountDeletionToken.used_at.is_(None),
                )
                .values(used_at=datetime.now(UTC))
                .returning(models.AccountDeletionToken.id)
            )
        ).first()
        if used is None:
            await db.rollback()
            raise _invalid_token()
        await account_service.delete_user_account(db, user)
        await db.commit()
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 網頁刪除帳號失敗: {e}")
        raise HTTPException(status_code=500, detail="刪除帳號失敗，請稍後再試")


# ---------------------------------------------------------------- 頁面

_STYLE = """
:root { color-scheme: light dark; --bg:#f6f7f9; --card:#fff; --text:#1d2129; --muted:#5c6370;
  --line:#e3e6ea; --danger:#c62828; --danger-text:#fff; --ok:#2e7d32; }
@media (prefers-color-scheme: dark) { :root { --bg:#14161a; --card:#1e2127; --text:#e8eaed;
  --muted:#a3a9b3; --line:#30343b; --danger:#ef5350; --danger-text:#14161a; --ok:#81c784; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
  font-family: -apple-system, "PingFang TC", "Noto Sans TC", "Microsoft JhengHei", sans-serif; line-height:1.6; }
main { max-width:560px; margin:0 auto; padding:24px 16px 48px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:20px; margin-top:16px; }
h1 { font-size:1.4rem; margin:0 0 4px; }
h2 { font-size:1rem; margin:0 0 8px; }
p, li { color:var(--text); }
.muted { color:var(--muted); font-size:.92rem; }
ul { padding-left:1.2em; margin:0; }
label { display:block; font-weight:600; margin-bottom:6px; }
input[type=email] { width:100%; padding:12px; font-size:1rem; border:1px solid var(--line);
  border-radius:8px; background:var(--bg); color:var(--text); }
button { width:100%; margin-top:12px; padding:12px; font-size:1rem; font-weight:600; border:0;
  border-radius:8px; background:var(--danger); color:var(--danger-text); cursor:pointer; }
button:disabled { opacity:.6; cursor:default; }
.msg { margin-top:12px; font-weight:600; }
.msg.ok { color:var(--ok); }
.msg.err { color:var(--danger); }
"""


def _page(title: str, body: str, script: str = "") -> HTMLResponse:
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="robots" content="noindex" />
<title>{html.escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body><main>{body}</main>{f"<script>{script}</script>" if script else ""}</body>
</html>"""
    )


_SCOPE = """
<div class="card">
  <h2>會刪除的資料</h2>
  <ul>
    <li>個人資料：Email、暱稱、生日、頭貼、Google／LINE 登入綁定、會員條碼、電子發票載具</li>
    <li>紅利點數餘額與點數明細、每日簽到紀錄</li>
    <li>尚未使用的優惠券</li>
    <li>收藏、好友關係、推薦好友紀錄、通知紀錄</li>
    <li>推播裝置與所有登入狀態（所有裝置都會被登出）</li>
  </ul>
</div>
<div class="card">
  <h2>會保留的資料（已匿名化，無法再連回您本人）</h2>
  <ul>
    <li>訂單與消費紀錄（網購、堂食、門市結帳），作為交易紀錄保存；訂單上的電子發票載具會清除</li>
    <li>已使用過的優惠券</li>
  </ul>
  <p class="muted">刪除後立即生效、無法復原。原本的 Email 之後可以重新註冊成新帳號。</p>
</div>
"""


@page_router.get("/account-deletion", response_class=HTMLResponse)
async def account_deletion_page():
    body = f"""
<h1>申請刪除帳號</h1>
<p class="muted">不用安裝 App 也可以在這裡申請。我們會寄確認信到您的帳號 Email，點信中連結並再確認一次後，帳號會立即刪除。</p>
{_SCOPE}
<div class="card">
  <form id="f" novalidate>
    <label for="email">帳號 Email</label>
    <input id="email" type="email" autocomplete="email" required placeholder="name@example.com" />
    <button id="b" type="submit">寄送確認信</button>
  </form>
  <p id="m" class="msg" role="status" aria-live="polite"></p>
  <p class="muted">用 LINE 登入且沒有 Email 的帳號，請在 App 的「設定 → 刪除帳號」操作。</p>
</div>
"""
    script = """
const f = document.getElementById('f'), b = document.getElementById('b'), m = document.getElementById('m');
f.addEventListener('submit', async (e) => {
  e.preventDefault();
  const email = document.getElementById('email').value.trim();
  if (!/^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(email)) { m.className = 'msg err'; m.textContent = '請輸入正確的 Email'; return; }
  b.disabled = true; m.className = 'msg'; m.textContent = '送出中…';
  try {
    const r = await fetch('/api/v1/account-deletion/request', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email})
    });
    if (r.status === 204) {
      m.className = 'msg ok';
      m.textContent = '如果這個 Email 有註冊，我們已寄出確認信，請在 30 分鐘內點信中連結。';
    } else if (r.status === 422) {
      m.className = 'msg err'; m.textContent = '請輸入正確的 Email';
    } else { throw new Error(r.status); }
  } catch (err) {
    m.className = 'msg err'; m.textContent = '送出失敗，請稍後再試';
  } finally { b.disabled = false; }
});
"""
    return _page("申請刪除帳號", body, script)


@page_router.get("/account-deletion/confirm", response_class=HTMLResponse)
async def account_deletion_confirm_page(token: str = "", db: AsyncSession = Depends(get_db)):
    """只顯示確認頁，不會刪除也不會消耗 token（信箱掃描機器人點開也沒事）。"""
    row = await _valid_token_row(db, token) if token else None
    user = await _active_user_by_email(db, row.email) if row else None
    if user is None:
        return _page(
            "連結已失效",
            '<h1>連結已失效</h1><div class="card"><p>連結已失效，請重新申請。</p>'
            '<p><a href="/account-deletion">重新申請刪除帳號</a></p></div>',
        )
    if user.role == "staff":
        return _page(
            "無法刪除",
            '<h1>無法刪除</h1><div class="card"><p>店員帳號無法自行刪除，請聯絡管理者。</p></div>',
        )
    body = f"""
<h1>確認刪除帳號</h1>
<div class="card">
  <p>即將刪除的帳號：<strong>{html.escape(mask_email(user.email))}</strong></p>
  <button id="b" type="button">確認刪除帳號</button>
  <p id="m" class="msg" role="status" aria-live="polite"></p>
</div>
{_SCOPE}
"""
    script = f"""
const token = {json.dumps(token).replace("<", chr(92) + "u003c")};
const b = document.getElementById('b'), m = document.getElementById('m');
b.addEventListener('click', async () => {{
  b.disabled = true; m.className = 'msg'; m.textContent = '處理中…';
  try {{
    const r = await fetch('/api/v1/account-deletion/confirm', {{
      method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{token}})
    }});
    if (r.status === 204) {{
      b.remove(); m.className = 'msg ok'; m.textContent = '帳號已刪除。感謝您的使用。';
      return;
    }}
    m.className = 'msg err';
    if (r.status === 403) m.textContent = '店員帳號無法自行刪除，請聯絡管理者';
    else if (r.status === 400) m.textContent = '連結已失效，請重新申請';
    else m.textContent = '刪除失敗，請稍後再試';
    if (r.status !== 400 && r.status !== 403) b.disabled = false;
  }} catch (err) {{
    m.className = 'msg err'; m.textContent = '刪除失敗，請稍後再試'; b.disabled = false;
  }}
}});
"""
    return _page("確認刪除帳號", body, script)
