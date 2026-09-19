# app/schemas/dine_in_order.py
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class DineInOrderItemCreate(BaseModel):
    menu_item_id: int
    quantity: int = Field(gt=0)


class DineInOrderCreate(BaseModel):
    """
    2026-09 多門市支援 Phase 3：table_id 改成必填——Phase 1/2 期間曾經接受舊版
    自由文字 table_number（沒有門市概念），兩邊前端（mynotification／staff-scanner）
    都已確認改用「選桌位」流程並實機測過，才收緊這裡不再接受純 table_number。
    """

    table_id: int
    items: List[DineInOrderItemCreate] = Field(min_length=1)
    # 要折抵的優惠券（可選），規則同 /orders：跟到店核銷是不同通路，直接折抵生效
    coupon_id: Optional[int] = None
    # 要折抵的點數（可選），規則同 /orders：1 點 = NT$1，單筆最高折抵「券後金額」50%
    use_points: int = Field(default=0, ge=0)


class DineInOrderItemOut(BaseModel):
    menu_item_id: int
    name: str
    quantity: int
    unit_price: float
    subtotal: float

    class Config:
        from_attributes = True


class DineInOrderOut(BaseModel):
    id: int
    # 2026-09 staff-scanner 提出：店員接單列表原本完全看不出一筆訂單是誰下的，
    # 曾經誤把別人測試帳號留下的舊訂單當成自己的。刻意只回傳 user_id（數字），
    # 不回傳 email/姓名，避免店員看到顧客個資——單純讓人能分辨「是不是同一顧客」
    user_id: int
    table_number: str
    table_id: Optional[int] = None
    restaurant_id: Optional[int] = None
    status: str
    total_amount: float
    coupon_id: Optional[int] = None
    coupon_title: Optional[str] = None
    coupon_discount: float = 0
    points_used: int = 0
    points_discount: float = 0
    points_earned: int = 0
    payment_method: Optional[str] = None
    created_at: datetime
    items: List[DineInOrderItemOut]

    class Config:
        from_attributes = True


class DineInOrderStatusUpdate(BaseModel):
    """
    2026-09 堂食付款/核銷流程：pending→served（出餐/用餐完畢，等待收款）→
    completed（已收款，終點，觸發點數入帳）。兩個轉換都只能照順序、不能跳過
    （endpoint 端會檢查目前狀態，不符合回 409）。payment_method 只有轉成
    completed 時才需要帶（endpoint 端驗證，沒帶回 400），轉 served 不需要、
    帶了也會被忽略。付款當下不會重新選點數/優惠券——那是建單當下就已經套用、
    算進 total_amount 的，這裡只是記錄收款方式並確認收款完成。
    """

    status: Literal["served", "completed"]
    payment_method: Optional[Literal["cash", "jkopay"]] = None
