from fastapi import APIRouter
# 這裡不寫 prefix，因為會由上一層 api.py 統一分配
router = APIRouter()

@router.get("/")
async def get_users():
    return [{"id": 1, "username": "admin"}, {"id": 2, "username": "guest"}]

@router.get("/{user_id}")
async def get_user_by_id(user_id: int):
    return {"id": user_id, "username": f"user_{user_id}"}
