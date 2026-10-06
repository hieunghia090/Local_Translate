from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import db
from app.config import get_settings

router = APIRouter()


@router.get("/health")
async def health() -> JSONResponse:
    ok = await db.ping()
    body = {
        "status": "ok" if ok else "degraded",
        "db": "ok" if ok else "down",
        "deepseek_key": bool(get_settings().deepseek_api_key.strip()),
    }
    return JSONResponse(body, status_code=200 if ok else 503)
