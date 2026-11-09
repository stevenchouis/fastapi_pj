# app/services/einvoice_service.py
"""
2026-11 電子發票載具（mynotification plan-einvoice-carrier.md，使用者 2026-11-09 定案）。

這期只做「綁定＋出示條碼＋訂單記錄載具」，不真正開發票；只檢查格式、不向財政部驗證。
財政部手機條碼格式：「/」加 7 碼，每碼為 0-9、A-Z、「.」、「+」、「-」。
"""
import re

CARRIER_PATTERN = re.compile(r"^/[0-9A-Z.+\-]{7}$")


def normalize_carrier(value: str) -> str:
    """去掉所有空白（含中間的）並轉大寫。"""
    return "".join(value.split()).upper()


def is_valid_carrier(value: str) -> bool:
    """格式檢查（已 normalize 過的值）。"""
    return bool(CARRIER_PATTERN.fullmatch(value))
