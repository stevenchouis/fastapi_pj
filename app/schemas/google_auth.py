# app/schemas/google_auth.py
from pydantic import BaseModel


class GoogleLoginRequest(BaseModel):
    id_token: str
