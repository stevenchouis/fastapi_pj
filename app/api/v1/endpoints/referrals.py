# app/api/v1/endpoints/referrals.py
"""2026-11 推薦好友端點，規則見 app/services/referral_service.py 開頭的說明。"""
import html
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.database_async import get_db
from app.models import Coupon, Referral, User
from app.schemas.coupon import CouponOut
from app.schemas.referral import (
    ReferralApplyOut,
    ReferralApplyRequest,
    ReferralMeOut,
    ReferredByOut,
)
from app.services import referral_service

router = APIRouter()

# 錯誤格式比照紅利點數：detail 是帶 error_code 的物件（跟前端對過的契約）
APPLY_ERRORS = {
    "invalid": (400, "invalid_referral_code", "推薦碼不存在"),
    "self": (400, "self_referral", "不能填自己的推薦碼"),
    "already": (409, "already_referred", "已經填過推薦碼了"),
    "window_closed": (409, "apply_window_closed", "已超過補填期限或已有消費紀錄"),
}


def apply_error(outcome: str) -> HTTPException:
    status_code, error_code, message = APPLY_ERRORS[outcome]
    return HTTPException(
        status_code=status_code, detail={"error_code": error_code, "message": message}
    )


LANDING_TITLE = "寶雅電子商城 好友推薦"


def _landing_html(body: str, deep_link: str | None) -> str:
    # 有 deep link 才自動跳轉一次（手法比照 /login/magic-link/redirect）；推薦碼無效時不跳
    auto = f"<script>window.location.replace({json.dumps(deep_link)});</script>" if deep_link else ""
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{LANDING_TITLE}</title>
<style>
  body {{ font-family: -apple-system, "PingFang TC", "Noto Sans TC", sans-serif; margin: 0;
         padding: 32px 20px; text-align: center; color: #222; background: #fafafa; }}
  h1 {{ font-size: 20px; margin: 0 0 24px; }}
  .code {{ font-size: 40px; font-weight: 700; letter-spacing: 6px; margin: 8px 0 16px; }}
  .btn {{ display: inline-block; margin: 24px 0 16px; padding: 14px 32px; border-radius: 999px;
          background: #e60012; color: #fff; text-decoration: none; font-size: 17px; }}
  .note {{ font-size: 13px; color: #777; }}
</style>
{auto}
</head>
<body>
<h1>{LANDING_TITLE}</h1>
{body}
</body>
</html>"""


@router.get("/landing", response_class=HTMLResponse)
async def referral_landing(code: str = ""):
    """
    推薦連結的 https 中繼頁（公開，不需登入）：LINE 不會把 mynotification:// 自訂 scheme
    變成可點的連結，所以分享出去的是這個 https 網址，再由這頁跳轉到 App
    （做法比照 /login/magic-link/redirect）。純顯示，不消耗任何東西，可以重複開。

    只檢查推薦碼格式（6 碼、字元都在 CODE_ALPHABET 內），**刻意不查 DB**，避免這頁
    被拿來試出哪些推薦碼存在；碼是否真的存在留到註冊時由 /users/register 判斷。
    通過格式檢查的碼只含大寫英數，仍一律 HTML escape／URL encode 後才輸出。
    """
    normalized = referral_service.normalize_code(code)
    if not referral_service.is_valid_code_format(normalized):
        body = '<p class="code">推薦碼無效</p><p class="note">請向分享給你的朋友確認連結是否完整。</p>'
        return HTMLResponse(content=_landing_html(body, None))

    deep_link = f"mynotification://register?ref={quote(normalized)}"
    safe_code = html.escape(normalized)
    safe_link = html.escape(deep_link, quote=True)
    body = f"""<p class="code">{safe_code}</p>
<p>註冊時輸入這組推薦碼，就能多拿一張 $50 優惠券</p>
<a class="btn" href="{safe_link}">開啟 App 註冊</a>
<p class="note">還沒安裝 App？請先安裝後，在註冊頁輸入上面的推薦碼</p>"""
    return HTMLResponse(content=_landing_html(body, deep_link))


@router.get("/me", response_model=ReferralMeOut)
async def get_my_referral(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """我的推薦碼（第一次查詢才產生）、推薦成果、誰推薦我、能不能補填。"""
    user_id = current_user.id
    had_code = bool(current_user.referral_code)
    try:
        code = await referral_service.ensure_referral_code(db, current_user)
        if not had_code:
            await db.commit()
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 產生推薦碼失敗: {e}")
        raise HTTPException(status_code=500, detail="產生推薦碼失敗")

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()

    counts = (
        await db.execute(
            select(
                func.count().filter(Referral.first_purchase_at.isnot(None)),
                func.count().filter(Referral.first_purchase_at.is_(None)),
            ).where(Referral.referrer_id == user_id)
        )
    ).one()

    referred_by = None
    referral = await referral_service.get_referral_of(db, user_id)
    if referral:
        referrer = (
            await db.execute(select(User).where(User.id == referral.referrer_id))
        ).scalar_one()
        referred_by = ReferredByOut(display_name=referral_service.referral_display_name(referrer))

    can_apply, deadline = await referral_service.can_apply(db, user)
    return ReferralMeOut(
        code=code,
        successful_count=counts[0],
        pending_count=counts[1],
        rewarded_this_year=await referral_service.count_rewarded_this_year(db, user_id),
        yearly_limit=referral_service.REFERRER_YEARLY_LIMIT,
        referred_by=referred_by,
        can_apply_code=can_apply,
        apply_deadline=deadline,
    )


@router.post("/apply", response_model=ReferralApplyOut, status_code=201)
async def apply_referral_code(
    payload: ReferralApplyRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(deps.get_current_user),
):
    """
    補填推薦碼（給 Google／LINE／Magic Link 自動建立、註冊時沒機會填碼的帳號）：
    註冊 7 天內、還沒消費過、還沒被推薦過才行。記錄推薦關係＋發 $50 券在同一個 transaction。
    """
    try:
        outcome, referrer, coupon = await referral_service.apply_referral(
            db, current_user, payload.code
        )
        if outcome != "ok":
            await db.rollback()
            raise apply_error(outcome)
        coupon_id = coupon.id
        display_name = referral_service.referral_display_name(referrer)
        await db.commit()
    except HTTPException:
        raise
    except IntegrityError:
        # referee_id 唯一限制兜底：兩個請求同時補填，後到的撞限制
        await db.rollback()
        raise apply_error("already")
    except Exception as e:
        await db.rollback()
        print(f"DEBUG: 補填推薦碼失敗: {e}")
        raise HTTPException(status_code=500, detail="補填推薦碼失敗")

    coupon = (await db.execute(select(Coupon).where(Coupon.id == coupon_id))).scalar_one()
    return ReferralApplyOut(
        coupon=CouponOut.model_validate(coupon),
        referred_by=ReferredByOut(display_name=display_name),
    )
