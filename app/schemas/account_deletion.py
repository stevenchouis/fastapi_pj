# app/schemas/account_deletion.py
from pydantic import BaseModel, EmailStr


class AccountDeletionRequest(BaseModel):
    """POST /account-deletion/request 的 body"""

    email: EmailStr


class AccountDeletionConfirm(BaseModel):
    """POST /account-deletion/confirm 的 body"""

    token: str
