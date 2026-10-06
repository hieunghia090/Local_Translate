import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import HanvietUpdate
from app.services import hanviet as hanviet_service

router = APIRouter()


def schedule_fill(request: Request, background: BackgroundTasks, book_id: uuid.UUID) -> None:
    """Mục 6a bước 3: bổ sung âm còn thiếu sau khi tạo truyện, thêm chương hoặc thêm term."""
    if request.app.state.hanviet_autofill:
        background.add_task(hanviet_service.fill_missing, book_id, request.app.state.deepseek_client_factory)


@router.get("/hanviet")
async def get_hanviet(chars: str = Query(..., min_length=1, max_length=200), session: AsyncSession = Depends(get_session)):
    return {"items": await hanviet_service.lookup(session, chars)}


@router.put("/hanviet/{char}")
async def put_hanviet(char: str, body: HanvietUpdate, session: AsyncSession = Depends(get_session)):
    return await hanviet_service.set_char(session, char, body.readings)
