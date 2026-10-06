import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import ExportRequest
from app.services import exports

router = APIRouter()
ChapterNo = Annotated[int | None, Query(ge=1, le=2_147_483_647)]


@router.get("/books/{book_id}/exports/preview")
async def preview(book_id: uuid.UUID, scope: Literal["translated", "reviewed", "range"] = "translated",
                  from_no: ChapterNo = None, to_no: ChapterNo = None,
                  session: AsyncSession = Depends(get_session)):
    return await exports.preview(session, book_id, scope, from_no, to_no)


@router.post("/books/{book_id}/exports", status_code=202)
async def create_export(book_id: uuid.UUID, body: ExportRequest, background: BackgroundTasks,
                        session: AsyncSession = Depends(get_session)):
    exp = await exports.start_export(session, book_id, body)
    background.add_task(exports.run_export, exp.id)
    return {"export_id": str(exp.id)}


@router.get("/exports/{export_id}")
async def get_export(export_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return exports.export_view(await exports.get_export(session, export_id))


@router.get("/exports/{export_id}/download")
async def download(export_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    path, media_type, name = await exports.download_target(session, export_id)
    return FileResponse(path, media_type=media_type, filename=name)
