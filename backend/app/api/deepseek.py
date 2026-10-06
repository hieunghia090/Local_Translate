import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deepseek.catalog import DEEPSEEK_MODELS
from app.deepseek.client import MAX_RATE_WAITS, DeepSeekError, RateBudget, build_request

TEST_TIMEOUT = 20.0  # giây: nút "Thử kết nối" không được treo lâu
TEST_MAX_RATE_WAIT = 10.0
from app.models import WorkerState
from app.schemas import AiModelUpdate, EstimateRequest, FoundationUpdate, NoteCreate, NoteUpdate
from app.services import ai_cost, foundation, queue, review

router = APIRouter()


@router.get("/books/{book_id}/foundation")
async def get_foundation(book_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await foundation.get_foundation(session, book_id)


@router.put("/books/{book_id}/foundation")
async def put_foundation(book_id: uuid.UUID, body: FoundationUpdate, session: AsyncSession = Depends(get_session)):
    return await foundation.set_foundation(session, book_id, body.foundation_prompt)


@router.post("/books/{book_id}/foundation/reset")
async def reset_foundation(book_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await foundation.set_foundation(session, book_id, None)


@router.get("/books/{book_id}/foundation/preview")
async def preview_foundation(book_id: uuid.UUID, chapter_no: int = Query(..., ge=1, le=2_147_483_647),
                             session: AsyncSession = Depends(get_session)):
    return await foundation.preview(session, book_id, chapter_no)


@router.post("/books/{book_id}/estimate")
async def estimate(book_id: uuid.UUID, body: EstimateRequest, session: AsyncSession = Depends(get_session)):
    return await ai_cost.estimate_batch(session, book_id, action=body.action, chapter_ids=body.chapter_ids,
                                        statuses=body.filter.status if body.filter else None, model_id=body.model_id)


@router.get("/books/{book_id}/usage")
async def usage(book_id: uuid.UUID, month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                session: AsyncSession = Depends(get_session)):
    return await ai_cost.usage_month(session, book_id, month)


@router.get("/chapters/{chapter_id}/notes")
async def list_notes(chapter_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return {"items": await review.list_notes(session, chapter_id)}


@router.post("/chapters/{chapter_id}/notes", status_code=201)
async def create_note(chapter_id: uuid.UUID, body: NoteCreate, session: AsyncSession = Depends(get_session)):
    return await review.create_note(session, chapter_id, type_=body.type, content=body.content)


@router.patch("/notes/{note_id}")
async def update_note(note_id: uuid.UUID, body: NoteUpdate, session: AsyncSession = Depends(get_session)):
    return await review.update_note(session, note_id, body.model_dump(exclude_unset=True))


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await review.delete_note(session, note_id)
    return Response(status_code=204)


@router.get("/ai-models")
async def list_models(session: AsyncSession = Depends(get_session)):
    return {"items": await ai_cost.list_models(session)}


@router.patch("/ai-models/{model_id}")
async def update_model(model_id: str, body: AiModelUpdate, session: AsyncSession = Depends(get_session)):
    return await ai_cost.update_model(session, model_id, body.model_dump(exclude_unset=True))


@router.get("/deepseek/status")
async def deepseek_status(session: AsyncSession = Depends(get_session)):
    state = await session.get(WorkerState, "deepseek")
    settings = get_settings()
    paused = bool(state and state.paused)
    reason = state.paused_reason if state else None
    return {"key_present": bool(settings.deepseek_api_key.strip()), "key_masked": settings.masked_deepseek_key(),
            "paused": paused, "paused_reason": reason, "message": queue.pause_message(paused, reason)}


@router.post("/deepseek/test")
async def deepseek_test(request: Request):
    """Gọi thử một request rất nhỏ (cũng tắt thinking), để kiểm tra key và model."""
    model = get_settings().deepseek_default_model
    model = model if model in DEEPSEEK_MODELS else "deepseek-v4-pro"
    body = build_request(model, [{"role": "user", "content": "Trả lời đúng một chữ: OK"}], temperature=0, max_tokens=8)
    try:
        # ngân sách 429 chỉ còn 1 lần chờ, timeout ngắn: đây là kiểm tra nhanh, không phải job dịch
        c = await request.app.state.deepseek_client_factory().chat(
            body, budget=RateBudget(waits=MAX_RATE_WAITS - 1), timeout=TEST_TIMEOUT, max_rate_wait=TEST_MAX_RATE_WAIT)
    except DeepSeekError as e:
        return {"ok": False, "model": model, "status": e.status, "message": str(e)}
    return {"ok": True, "model": model, "latency_ms": c.latency_ms, "reasoning_tokens": c.usage.reasoning_tokens}
