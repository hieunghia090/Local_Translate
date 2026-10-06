import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Path, Query, Request, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.api.hanviet import schedule_fill
from app.core.splitter import order_files
from app.core.textio import SourceDecodeError, UnsupportedEncodingError, decode_source

from app.db import get_session
from app.errors import AppError
from app.models import CHAPTER_STATUSES
from app.schemas import BulkRequest, ChapterUpdate, NewChapterJson, SegmentPatch, TranslateRequest
from app.services import chapters, revisions
from app.services import glossary as glossary_service
from app.services.chapters import PAGE_MAX

router = APIRouter()
INT32_MAX = 2_147_483_647
Int32Path = Annotated[int, Path(ge=0, le=INT32_MAX)]


def _parse_statuses(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    values = [v.strip() for v in raw.split(",") if v.strip()]
    bad = [v for v in values if v not in CHAPTER_STATUSES]
    if bad:
        raise AppError("INVALID_STATUS", "Trạng thái chương không hợp lệ", 422, {"values": bad})
    return values


@router.get("/books/{book_id}/chapters")
async def list_chapters(
    book_id: uuid.UUID,
    status: str | None = None,
    q: str | None = None,
    cursor: int | None = Query(None, ge=0, le=INT32_MAX),
    limit: int = Query(50, ge=1, le=PAGE_MAX),
    session: AsyncSession = Depends(get_session),
):
    return await chapters.list_chapters(
        session, book_id, statuses=_parse_statuses(status), q=q, cursor=cursor, limit=limit
    )


@router.post("/books/{book_id}/chapters/bulk")
async def bulk(book_id: uuid.UUID, body: BulkRequest, session: AsyncSession = Depends(get_session)):
    result = await chapters.bulk_action(
        session,
        book_id,
        action=body.action,
        chapter_ids=body.chapter_ids,
        statuses=body.filter.status if body.filter else None,
        keep_manual_edits=body.keep_manual_edits,
        engine=body.engine,
    )
    await session.commit()
    return result


@router.get("/books/{book_id}/chapters/by-no/{no}")
async def chapter_by_no(book_id: uuid.UUID, no: Int32Path, session: AsyncSession = Depends(get_session)):
    return await chapters.chapter_by_no(session, book_id, no)


@router.patch("/segments/{chapter_id}/{idx}")
async def patch_segment(chapter_id: uuid.UUID, idx: Int32Path, body: SegmentPatch, session: AsyncSession = Depends(get_session)):
    return await chapters.edit_segment(session, chapter_id, idx, dst=body.dst, revert=body.revert)


@router.post("/chapters/{chapter_id}/mark-reviewed")
async def mark_reviewed(chapter_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await chapters.mark_reviewed(session, chapter_id)


@router.post("/chapters/{chapter_id}/translate", status_code=202)
async def translate(chapter_id: uuid.UUID, body: TranslateRequest | None = None, session: AsyncSession = Depends(get_session)):
    body = body or TranslateRequest()
    job = await chapters.translate_chapter(
        session, chapter_id, priority=body.priority, keep_manual_edits=body.keep_manual_edits, engine=body.engine
    )
    return {"job_id": str(job.id)}


@router.put("/chapters/{chapter_id}/run-config")
async def put_run_config(chapter_id: uuid.UUID, body: dict | None = Body(None), session: AsyncSession = Depends(get_session)):
    return await chapters.set_run_config(session, chapter_id, body)


@router.patch("/chapters/{chapter_id}")
async def patch_chapter(chapter_id: uuid.UUID, body: ChapterUpdate, session: AsyncSession = Depends(get_session)):
    return await chapters.update_chapter(session, chapter_id, body)


@router.get("/chapters/{chapter_id}/revisions")
async def list_revisions(chapter_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await revisions.list_revisions(session, chapter_id)


@router.post("/chapters/{chapter_id}/revisions/{revision_id}/restore")
async def restore(chapter_id: uuid.UUID, revision_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await revisions.restore_revision(session, chapter_id, revision_id)


def _invalid_body() -> AppError:
    return AppError("VALIDATION_ERROR", "Dữ liệu gửi lên không hợp lệ", 422)


def _parse_after_no(raw) -> int | None:
    """Rỗng/thiếu = thêm cuối; số nguyên >= 0 = chèn sau chương đó; còn lại là lỗi 422."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise _invalid_body() from None
    if value < 0:
        raise _invalid_body()
    return value


async def _json_body(request: Request):
    try:
        return await request.json()
    except ValueError:  # gồm json.JSONDecodeError và UnicodeDecodeError
        raise _invalid_body() from None


async def _new_chapters_from_request(request: Request) -> tuple[list[chapters.NewChapter], int | None]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form(max_files=5000)
        try:
            uploads = [v for key in ("files[]", "files") for v in form.getlist(key) if isinstance(v, UploadFile)]
            if not uploads:
                raise AppError("NO_FILES", "Chưa chọn file nào", 422)
            after_no = _parse_after_no(form.get("after_no"))
            decoded = []
            for up in uploads:
                try:
                    decoded.append((up.filename or "chapter.txt", decode_source(await up.read()).text))
                except (SourceDecodeError, UnsupportedEncodingError) as e:
                    raise AppError("ENCODING", f"{up.filename}: {e}", 422, {"file": up.filename}) from e
        finally:
            await form.close()
        names = [n for n, _ in decoded]
        return [chapters.NewChapter(None, decoded[i][1]) for i in order_files(names)], after_no
    try:
        body = NewChapterJson.model_validate(await _json_body(request))
    except ValidationError as e:
        raise AppError("VALIDATION_ERROR", "Dữ liệu gửi lên không hợp lệ", 422,
                       {"errors": e.errors(include_url=False, include_context=False)}) from e
    return [chapters.NewChapter(body.title_zh, body.content)], body.after_no


@router.post("/books/{book_id}/chapters", status_code=201)
async def add_chapters(book_id: uuid.UUID, request: Request, background: BackgroundTasks,
                       session: AsyncSession = Depends(get_session)):
    items, after_no = await _new_chapters_from_request(request)
    created = await chapters.add_chapters(
        session, book_id, items, after_no=after_no, translator_factory=request.app.state.translator_factory
    )
    background.add_task(glossary_service.recount_occurrences, book_id)
    schedule_fill(request, background, book_id)
    return {"chapters": created}


@router.put("/chapters/{chapter_id}/source")
async def replace_source(chapter_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)):
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        try:
            upload = form.get("file")
            if not isinstance(upload, UploadFile):
                raise AppError("NO_FILES", "Chưa chọn file", 422)
            data = await upload.read()
        finally:
            await form.close()
    else:
        payload = await _json_body(request)
        if not isinstance(payload, dict) or not isinstance(payload.get("content"), str):
            raise AppError("VALIDATION_ERROR", "Cần gửi content", 422)
        data = payload["content"].encode("utf-8")
    return await chapters.replace_source(session, chapter_id, data)


@router.delete("/chapters/{chapter_id}", status_code=204)
async def delete_chapter(chapter_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await chapters.delete_chapter(session, chapter_id)
    return Response(status_code=204)
