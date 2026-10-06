import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import FixIds
from app.services import review

router = APIRouter()


@router.post("/chapters/{chapter_id}/ai-review", status_code=202)
async def ai_review(chapter_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    job = await review.start_ai_review(session, chapter_id)
    return {"job_id": str(job.id)}


@router.get("/chapters/{chapter_id}/review-fixes")
async def list_fixes(chapter_id: uuid.UUID, status: Literal["pending", "applied", "rejected"] | None = None,
                     session: AsyncSession = Depends(get_session)):
    return {"items": await review.list_fixes(session, chapter_id, status)}


@router.post("/review-fixes/apply")
async def apply_fixes(body: FixIds, session: AsyncSession = Depends(get_session)):
    return await review.apply_fixes(session, body.ids)


@router.post("/review-fixes/reject")
async def reject_fixes(body: FixIds, session: AsyncSession = Depends(get_session)):
    return await review.reject_fixes(session, body.ids)