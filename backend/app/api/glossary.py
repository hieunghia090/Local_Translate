import json
import uuid
from typing import Literal

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.api.hanviet import schedule_fill
from app.db import get_session
from app.errors import AppError
from app.schemas import CopyGlossary, SnippetRequest, TermCreate, TermUpdate
from app.services import glossary

router = APIRouter()
Category = Literal["character", "location", "organization", "term", "rank", "realm", "item", "abbreviation"]


@router.get("/books/{book_id}/glossary")
async def list_terms(book_id: uuid.UUID, q: str | None = None, category: Category | None = None,
                     sort: Literal["src", "occurrence"] = "src", send: Literal["all", "send", "predictable"] = "all",
                     session: AsyncSession = Depends(get_session)):
    items = await glossary.list_terms(session, book_id, q=q, category=category, sort=sort, send=send)
    return {"items": items, "summary": await glossary.summary(session, book_id)}


@router.post("/books/{book_id}/glossary", status_code=201)
async def create_term(book_id: uuid.UUID, body: TermCreate, request: Request, background: BackgroundTasks,
                      session: AsyncSession = Depends(get_session)):
    term = await glossary.create_term(session, book_id, body)
    background.add_task(glossary.recount_occurrences, book_id)
    schedule_fill(request, background, book_id)
    return glossary.term_view(term)


@router.patch("/glossary/{term_id}")
async def update_term(term_id: uuid.UUID, body: TermUpdate, session: AsyncSession = Depends(get_session)):
    return await glossary.update_term(session, term_id, body)


@router.delete("/glossary/{term_id}", status_code=204)
async def delete_term(term_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await glossary.delete_term(session, term_id)
    return Response(status_code=204)


@router.post("/books/{book_id}/glossary/import")
async def import_terms(book_id: uuid.UUID, request: Request, background: BackgroundTasks,
                       session: AsyncSession = Depends(get_session)):
    form = await request.form()
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise AppError("NO_FILES", "Chưa chọn file glossary", 422)
        on_conflict = str(form.get("on_conflict") or "ask")
        if on_conflict not in ("keep", "overwrite", "ask"):
            raise AppError("VALIDATION_ERROR", "on_conflict phải là keep, overwrite hoặc ask", 422)
        try:
            overwrite = set(json.loads(str(form.get("overwrite") or "[]")))
        except ValueError as e:
            raise AppError("VALIDATION_ERROR", "overwrite phải là mảng JSON", 422) from e
        items = glossary.parse_import(await upload.read(), upload.filename or "")
    finally:
        await form.close()
    result = await glossary.import_terms(session, book_id, items, on_conflict=on_conflict, overwrite=overwrite)
    if on_conflict != "ask":
        background.add_task(glossary.recount_occurrences, book_id)
        schedule_fill(request, background, book_id)
    return result


@router.get("/books/{book_id}/glossary/export")
async def export_terms(book_id: uuid.UUID, format: Literal["tsv", "json"] = "tsv",
                       session: AsyncSession = Depends(get_session)):
    body, media, name = await glossary.export_terms(session, book_id, format)
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/books/{book_id}/glossary/copy")
async def copy_terms(book_id: uuid.UUID, body: CopyGlossary, request: Request, background: BackgroundTasks,
                     session: AsyncSession = Depends(get_session)):
    result = await glossary.copy_terms(session, book_id, body.from_book_id)
    background.add_task(glossary.recount_occurrences, book_id)
    schedule_fill(request, background, book_id)
    return result


@router.post("/translate/snippet")
async def snippet(body: SnippetRequest, request: Request):
    vi = await anyio.to_thread.run_sync(glossary.translate_snippet, body.text, request.app.state.translator_factory)
    return {"vi": vi}
