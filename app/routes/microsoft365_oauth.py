from fastapi import APIRouter

router = APIRouter(prefix="/microsoft365")

@router.get("/connect")
def connect():
    return {"status": "OAuth connect route"}

@router.get("/callback")
def callback():
    return {"status": "OAuth callback route"}
