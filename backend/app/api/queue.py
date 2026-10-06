import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.services import queue

router = APIRouter()


class EngineBody(BaseModel):
    engine: Literal["ct2", "deepseek"] = "ct2"


class MoveBody(BaseModel):
    position: int = Field(ge=0)


@router.get("/queue")
async def get_queue(book_id: uuid.UUID | None = None, session: AsyncSession = Depends(get_session)):
    return await queue.list_queue(session, book_id)


@router.post("/queue/pause")
async def pause_queue(body: EngineBody | None = None, session: AsyncSession = Depends(get_session)):
    result = await queue.set_paused(session, (body or EngineBody()).engine, True)
    await session.commit()
    return result


@router.post("/queue/resume")
async def resume_queue(body: EngineBody | None = None, session: AsyncSession = Depends(get_session)):
    result = await queue.set_paused(session, (body or EngineBody()).engine, False)
    await session.commit()
    return result


@router.patch("/jobs/{job_id}")
async def patch_job(job_id: uuid.UUID, body: MoveBody, session: AsyncSession = Depends(get_session)):
    result = await queue.move_job(session, job_id, body.position)
    await session.commit()
    return result


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    result = await queue.cancel_job(session, job_id)
    await session.commit()
    return result
