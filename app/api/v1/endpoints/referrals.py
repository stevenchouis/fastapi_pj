# app/api/v1/endpoints/referrals.py
"""2026-11 推薦好友端點，規則見 app/services/referral_service.py 開頭的說明。"""
from fastapi import APIRouter, Depends, HTTPException
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
