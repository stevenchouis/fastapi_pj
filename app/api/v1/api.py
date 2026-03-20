from fastapi import APIRouter
from app.api.v1.endpoints import users, items
# 建立 v1 的總路由
api_router = APIRouter()
# 註冊子路由
# prefix="/users" 代表這底下的路徑都會變成 /users/...
# tags 則會讓 Swagger UI 文件自動幫你分類
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(items.router, prefix="/items", tags=["Items"])
