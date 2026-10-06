import uuid
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.api.hanviet import schedule_fill
from app.schemas import ExtractRequest, GlossaryPreviewRequest, SuggestionAccept, SuggestionReject
from app.services import ai_cost, extraction, suggestions
from app.services import glossary as glossary_service

router = APIRouter()


@router.post("/books/{book_id}/glossary/extract", status_code=202)
async def extract(book_id: uuid.UUID, body: ExtractRequest, session: AsyncSession = Depends(get_session)):
    job, n = await extraction.start_extract(session, book_id, body)
    return {"job_id": str(job.id), "chapters": n}


@router.post("/books/{book_id}/glossary/extract/estimate")
async def extract_estimate(book_id: uuid.UUID, body: ExtractRequest, session: AsyncSession = Depends(get_session)):
    return await extraction.estimate_extract(session, book_id, body)


@router.get("/books/{book_id}/glossary/suggestions")
async def list_suggestions(book_id: uuid.UUID, status: Literal["pending", "accepted", "rejected"] | None = None,
                           limit: int = Query(200, ge=1, le=1000), offset: int = Query(0, ge=0),
                           session: AsyncSession = Depends(get_session)):
    return await suggestions.list_suggestions(session, book_id, status, limit=limit, offset=offset)


@router.post("/books/{book_id}/glossary/suggestions/accept")
async def accept_suggestions(book_id: uuid.UUID, body: SuggestionAccept, request: Request, background: BackgroundTasks,
                             session: AsyncSession = Depends(get_session)):
    result = await suggestions.accept(session, book_id, body.items, body.filter)
    if result["added"]:
        background.add_task(glossary_service.recount_occurrences, book_id)
        schedule_fill(request, background, book_id)
    return result


@router.post("/books/{book_id}/glossary/suggestions/reject")
async def reject_suggestions(book_id: uuid.UUID, body: SuggestionReject, session: AsyncSession = Depends(get_session)):
    return await suggestions.reject(session, book_id, body.ids, body.filter)


@router.get("/books/{book_id}/glossary/health")
async def glossary_health(book_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await suggestions.glossary_health(session, book_id)


@router.get("/books/{book_id}/glossary/send-stats")
async def send_stats(book_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await ai_cost.glossary_send_stats(session, book_id)


@router.post("/imports/{import_id}/glossary-preview")
async def glossary_preview(import_id: uuid.UUID, body: GlossaryPreviewRequest, request: Request,
                           session: AsyncSession = Depends(get_session)):
    return await extraction.preview_import(session, import_id, body, request.app.state.deepseek_client_factory())


@router.post("/imports/{import_id}/glossary-preview/estimate")
async def glossary_preview_estimate(import_id: uuid.UUID, body: GlossaryPreviewRequest,
                                    session: AsyncSession = Depends(get_session)):
    return await extraction.estimate_import(session, import_id, body)
