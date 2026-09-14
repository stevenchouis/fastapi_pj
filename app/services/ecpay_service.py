# app/services/ecpay_service.py
"""
綠界 ECPay 金流（AioCheckOut/V5）簽章工具與付款表單欄位組裝。

CheckMacValue 演算法是 ECPay 官方規定的固定流程（所有語言的官方/社群 SDK 都是同一套）：
1. 除去 CheckMacValue 本身，其餘參數依 key 做 ASCII 排序
2. 頭尾補上 HashKey/HashIV，組成 "HashKey=xxx&k1=v1&...&HashIV=xxx"
3. 對整串做 URL Encode（規則比照 .NET 的 UrlEncode），再整串轉小寫
4. 把 .NET UrlEncode 不會跳脫、但 Python quote_plus 會跳脫的符號還原回來
5. SHA256 雜湊，結果轉大寫（EncryptType=1 對應的就是 SHA256，ECPay 現行 API 只接受這個值，
   舊版 MD5 演算法已停用——2026-09-14 實機測試出現 CheckMacValue Error 才發現這裡原本誤用
   MD5，是實際發生過的 bug，不是理論上的風險，改動這段前務必先搞清楚 EncryptType 對應哪種演算法）

這一段邏輯非常固定、錯一步結果就完全不同，改動時務必對照 ECPay 官方技術文件重新核對。
"""
import hashlib
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from app.core.config import settings

if TYPE_CHECKING:
    from app.models import Order

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# ECPay 顧客端 App 的 deep link scheme——目前只有 mynotification 會用到網購訂單，
# 沒有多 App 分流的需求，直接寫死即可（跟 PushToken.app_id 那種需要區分多個 App 的情境不同）
CLIENT_BACK_SCHEME = "mynotification://order/{order_id}/result"


def _ecpay_url_encode(raw: str) -> str:
    encoded = quote_plus(raw).lower()
    return (
        encoded.replace("%2d", "-")
        .replace("%5f", "_")
        .replace("%2e", ".")
        .replace("%21", "!")
        .replace("%2a", "*")
        .replace("%28", "(")
        .replace("%29", ")")
    )


def generate_check_mac_value(params: dict, hash_key: str, hash_iv: str) -> str:
    """依 ECPay 規則計算 CheckMacValue（傳入的 params 若含 CheckMacValue 本身會被忽略）。"""
    filtered = {k: v for k, v in params.items() if k != "CheckMacValue"}
    sorted_items = sorted(filtered.items(), key=lambda kv: kv[0])
    raw = (
        f"HashKey={hash_key}&"
        + "&".join(f"{k}={v}" for k, v in sorted_items)
        + f"&HashIV={hash_iv}"
    )
    encoded = _ecpay_url_encode(raw)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest().upper()


def verify_check_mac_value(params: dict, hash_key: str, hash_iv: str) -> bool:
    """驗證 ECPay callback 帶來的 CheckMacValue 是否跟我方重新計算的一致。"""
    received = str(params.get("CheckMacValue", ""))
    computed = generate_check_mac_value(params, hash_key, hash_iv)
    return computed == received.upper()


def build_checkout_params(order: "Order") -> dict:
    """
    組出 AioCheckOut/V5 需要的表單欄位（含 CheckMacValue）。
    呼叫前 order.items 需已經 eager load 好（selectinload），否則存取 item.product 會噴 MissingGreenlet。

    注意：ECPay TotalAmount 不支援小數，這裡用 round() 四捨五入到整數元——
    目前商品價格資料多半就是整數，真的出現分位數字時金額會有 1 元內的無條件捨去/進位誤差，
    如果之後要嚴謹處理，需要另外跟業務確認捨入規則。
    """
    item_name = "#".join(
        f"{item.product.title} x{item.quantity}" for item in order.items
    )
    total_amount = round(Decimal(order.total_amount))

    params = {
        "MerchantID": settings.ECPAY_MERCHANT_ID,
        "MerchantTradeNo": order.merchant_trade_no,
        "MerchantTradeDate": datetime.now(TAIPEI_TZ).strftime("%Y/%m/%d %H:%M:%S"),
        "PaymentType": "aio",
        "TotalAmount": str(total_amount),
        "TradeDesc": "訂單付款",
        "ItemName": item_name,
        "ReturnURL": f"{settings.BASE_URL}{settings.API_V1_STR}/orders/ecpay/callback",
        "ChoosePayment": "Credit",
        "ClientBackURL": CLIENT_BACK_SCHEME.format(order_id=order.id),
        "EncryptType": "1",
    }
    params["CheckMacValue"] = generate_check_mac_value(
        params, settings.ECPAY_HASH_KEY, settings.ECPAY_HASH_IV
    )
    return params
