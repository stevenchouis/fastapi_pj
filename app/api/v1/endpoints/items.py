from fastapi import APIRouter

router = APIRouter()

@router.get("/")
async def get_items():
    return [{"item_id": "A1", "name": "Laptop"}, {"item_id": "B2", "name": "Mouse"}]

@router.post("/")
async def create_item(item_data: dict):
    return {"status": "created", "data": item_data}
