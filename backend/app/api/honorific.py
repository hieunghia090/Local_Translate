import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.honorific.engine import preview
from app.schemas import PreviewRequest, ReapplyRequest, RegisterRequest
from app.services import honorific

router = APIRouter()


@router.post("/books/{book_id}/honorific/reapply", status_code=202)
async def reapply(book_id: uuid.UUID, body: ReapplyRequest, background: BackgroundTasks,
                  session: AsyncSession = Depends(get_session)):
    job, ids = await honorific.start_reapply(session, book_id, body.chapter_ids)
    background.add_task(honorific.reapply_chapters, book_id, ids, job.id)
    return {"job_id": str(job.id), "chapters": len(ids)}


@router.put("/chapters/{chapter_id}/register")
async def put_register(chapter_id: uuid.UUID, body: RegisterRequest, background: BackgroundTasks,
                       session: AsyncSession = Depends(get_session)):
    chapter, job, ids = await honorific.set_register(session, chapter_id, body.route)
    if job is not None:
        background.add_task(honorific.reapply_chapters, chapter.book_id, ids, job.id)
    return {"register_override": body.route, "job_id": str(job.id) if job else None}


@router.post("/honorific/preview")
async def post_preview(body: PreviewRequest):
    return preview(body.src, body.dst_raw, body.route, body.config.model_dump())
