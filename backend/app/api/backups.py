import anyio
from fastapi import APIRouter

from app.services import backup

router = APIRouter()


@router.post("/backups", status_code=201)
async def post_backup():
    return await anyio.to_thread.run_sync(backup.create_backup)


@router.get("/backups")
async def get_backups():
    return {"items": await anyio.to_thread.run_sync(backup.list_backups)}
