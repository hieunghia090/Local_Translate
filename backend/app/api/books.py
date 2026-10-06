from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.hanviet import schedule_fill
from app.core.run_config import default_run_config
from app.db import get_session
from app.schemas import BookCreate, BookUpdate, Genre
from app.services import books
from app.services import glossary as glossary_service

router = APIRouter()


@router.post("/books", status_code=201)
async def post_book(body: BookCreate, request: Request, background: BackgroundTasks,
                    session: AsyncSession = Depends(get_session)):
    book = await books.create_book(session, body, request.app.state.translator_factory)
    background.add_task(glossary_service.recount_occurrences, book.id)
    schedule_fill(request, background, book.id)  # AC-4.18
    return {"id": str(book.id), "slug": book.slug}


@router.get("/run-config/defaults")
async def run_config_defaults(genre: Genre = "other"):
    return default_run_config(genre)

import uuid
from typing import Literal

from fastapi import Response

from app.services import library


@router.get("/books")
async def get_books(
    q: str | None = None,
    filter: Literal["all", "in_progress", "completed"] = "all",
    sort: Literal["recent", "name", "progress"] = "recent",
    session: AsyncSession = Depends(get_session),
):
    return {"items": await library.list_books(session, q=q, filter=filter, sort=sort), "next_cursor": None}


@router.get("/library/stats")
async def get_library_stats(session: AsyncSession = Depends(get_session)):
    return await library.library_stats(session)


@router.post("/books/{book_id}/open", status_code=204)
async def post_open_book(book_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await library.open_book(session, book_id)
    return Response(status_code=204)


@router.get("/books/{book_id}")
async def get_book(book_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await books.get_book_detail(session, book_id)


@router.patch("/books/{book_id}")
async def patch_book(book_id: uuid.UUID, body: BookUpdate, session: AsyncSession = Depends(get_session)):
    return await books.update_book(session, book_id, body)


@router.delete("/books/{book_id}", status_code=204)
async def delete_book(book_id: uuid.UUID, confirm: str = Query(...), session: AsyncSession = Depends(get_session)):
    await books.delete_book(session, book_id, confirm)
    return Response(status_code=204)
