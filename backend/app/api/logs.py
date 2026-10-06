import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import logs

router = APIRouter()

Level = Literal["info", "warn", "error"]
Source = Literal["translate", "glossary", "review", "system"]


@router.get("/books/{book_id}/logs")
async def get_logs(
    book_id: uuid.UUID,
    level: Level | None = None,
    source: Source | None = None,
    q: str | None = None,
    chapter_no: int | None = None,
    before: uuid.UUID | None = None,
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    items, next_cursor = await logs.list_logs(
        session, book_id, level=level, source=source, q=q, chapter_no=chapter_no, before=before, limit=limit
    )
    return {"items": items, "next_cursor": next_cursor}


@router.get("/books/{book_id}/logs/summary")
async def get_log_summary(book_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await logs.log_summary(session, book_id)


@router.get("/books/{book_id}/logs/export")
async def export_logs(
    book_id: uuid.UUID,
    level: Level | None = None,
    source: Source | None = None,
    q: str | None = None,
    chapter_no: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    lines = await logs.export_lines(session, book_id, level=level, source=source, q=q, chapter_no=chapter_no)
    body = "\n".join(lines) + ("\n" if lines else "")
    return Response(
        body,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="logs-{book_id}.jsonl"'},
    )


@router.delete("/books/{book_id}/logs", status_code=204)
async def delete_logs(book_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await logs.clear_logs(session, book_id)
    await session.commit()
    return Response(status_code=204)


@router.get("/logs/{log_id}")
async def get_log(log_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await logs.get_log(session, log_id)
