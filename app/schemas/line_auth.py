# app/schemas/line_auth.py
from pydantic import BaseModel


class LineLoginRequest(BaseModel):
    code: str
