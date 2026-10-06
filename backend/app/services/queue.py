import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.run_config import deep_merge, with_engine
from app.errors import AppError
from app.models import (
    ACTIVE_JOB_STATUSES,
    FINISHED_JOB_STATUSES,
    Book,
    Chapter,
    Job,
    JobSegment,
    WorkerState,
    utcnow,
)
from app.services import events, logs

RECENT_LIMIT = 20
QUEUE_KINDS = ("translate", "retranslate", "review")  # job áp lại xưng hô không tính vào hàng đợi dịch
TRANSLATE_KINDS = ("translate", "retranslate")
ENGINE_KINDS = {"ct2": TRANSLATE_KINDS, "deepseek": ("translate", "retranslate", "review", "ai_extract")}

PAUSE_MESSAGES = {
    "auth": "DeepSeek từ chối key / hết số dư. Kiểm tra trong Cài đặt.",
    "no_key": "Chưa có DEEPSEEK_API_KEY trong file .env. Thêm key rồi khởi động lại app.",
    "token_anomaly": "Token ra bất thường, đã tạm dừng để tránh tốn tiền. Xem log.",
    "failures": "5 chương DeepSeek liên tiếp bị lỗi, đã tạm dừng để tránh tốn tiền. Xem log.",
}


def pause_message(paused: bool, reason: str | None) -> str | None:
    if not paused:
        return None
    return PAUSE_MESSAGES.get(reason or "", "Pool DeepSeek đang tạm dừng.")


async def enqueue_chapters(
    session: AsyncSession, book: Book, chapters: list[Chapter], *, kind: str = "translate", options: dict | None = None,
    engine: str | None = None,
) -> list[Job]:
    """Tạo job cho các chương theo thứ tự `no`. Caller chọn chương hợp lệ và commit."""
    base = await session.scalar(select(func.coalesce(func.max(Job.position), 0)))
    jobs: list[Job] = []
    # chương đang translating / queued đã có job hoạt động (kể cả job vừa huỷ chưa được worker dọn): bỏ qua
    ordered = sorted((c for c in chapters if c.status not in ("translating", "queued")), key=lambda c: c.no)
    for i, ch in enumerate(ordered, start=1):
        cfg = deep_merge(book.run_config, ch.run_config_override or {})
        if engine is not None:  # chọn engine cho riêng lần dịch này (BR-6.5a, bulk "Dịch bằng DeepSeek")
            cfg = with_engine(cfg, engine)
        job = Job(
            book_id=book.id,
            chapter_id=ch.id,
            kind=kind,
            engine=cfg.get("engine", "ct2"),
            status="queued",
            progress=0,
            position=base + i,
            run_config=cfg,
            options=options or {},
            prev_chapter_status=ch.status,
            tokens_in=0,
            tokens_out=0,
            attempts=0,
        )
        ch.status = "queued"
        session.add(job)
        jobs.append(job)
    await session.flush()
    for job, ch in zip(jobs, ordered):
        await events.emit_job(session, job)
        await events.emit_chapter(session, ch)
    if jobs:
        await logs.write_log(
            session, level="info", source="system", book_id=book.id, message=f"Thêm {len(jobs)} chương vào hàng đợi"
        )
        await events.emit_book_stats(session, book.id)
    return jobs


async def enqueue_review(session: AsyncSession, book: Book, chapters: list[Chapter]) -> list[Job]:
    """Job soát bằng DeepSeek (spec 08 mục 5). Không đổi trạng thái chương; bỏ qua chương đang có job hoạt động."""
    ids = [c.id for c in chapters]
    busy = set((await session.scalars(
        select(Job.chapter_id).where(Job.chapter_id.in_(ids), Job.status.in_(ACTIVE_JOB_STATUSES))
    )).all()) if ids else set()
    ordered = sorted((c for c in chapters if c.id not in busy and c.status not in ("queued", "translating")),
                     key=lambda c: c.no)
    base = await session.scalar(select(func.coalesce(func.max(Job.position), 0)))
    jobs: list[Job] = []
    for i, ch in enumerate(ordered, start=1):
        job = Job(book_id=book.id, chapter_id=ch.id, kind="review", engine="deepseek", status="queued", progress=0,
                  position=base + i, run_config=deep_merge(book.run_config, ch.run_config_override or {}), options={},
                  prev_chapter_status=None, tokens_in=0, tokens_out=0, attempts=0)
        session.add(job)
        jobs.append(job)
    await session.flush()
    for job in jobs:
        await events.emit_job(session, job)
    if jobs:
        await logs.write_log(session, level="info", source="system", book_id=book.id,
                             message=f"Thêm {len(jobs)} chương vào hàng đợi soát DeepSeek")
        await events.emit_book_stats(session, book.id)
    return jobs


async def enqueue_extract(session: AsyncSession, book: Book, *, chapter_ids: list[uuid.UUID], model: str,
                          categories: list[str], auto: bool = False) -> Job:
    """Job trích glossary (spec 04 BR-4.9, BR-8.17). Không gắn với một chương: danh sách chương ở options."""
    base = await session.scalar(select(func.coalesce(func.max(Job.position), 0)))
    job = Job(book_id=book.id, chapter_id=None, kind="ai_extract", engine="deepseek", status="queued", progress=0,
              position=base + 1, run_config=book.run_config,
              options={"chapter_ids": [str(c) for c in chapter_ids], "model": model, "categories": list(categories),
                       "auto": auto},
              prev_chapter_status=None, tokens_in=0, tokens_out=0, attempts=0)
    session.add(job)
    await session.flush()
    await events.emit_job(session, job)
    await logs.write_log(session, level="info", source="glossary", book_id=book.id, job_id=job.id,
                         message=("Thêm job tự trích glossary sau khi dịch" if auto
                                  else f"Thêm job trích glossary bằng AI ({len(chapter_ids)} chương)"))
    return job


def job_view(job: Job, chapter: Chapter | None) -> dict:
    duration = None
    if job.started_at and job.finished_at:
        duration = int((job.finished_at - job.started_at).total_seconds() * 1000)
    return {
        "id": str(job.id),
        "book_id": str(job.book_id),
        "chapter_id": str(job.chapter_id) if job.chapter_id else None,
        "chapter_no": chapter.no if chapter else None,
        "chapter_title": (chapter.title_vi or chapter.title_zh) if chapter else None,
        "kind": job.kind,
        "engine": job.engine,
        "status": job.status,
        "progress": job.progress,
        "position": job.position,
        "tokens_in": job.tokens_in,
        "tokens_out": job.tokens_out,
        "error": job.error,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "duration_ms": duration,
    }


async def _paused_map(session: AsyncSession) -> dict[str, bool]:
    return dict((await session.execute(select(WorkerState.engine, WorkerState.paused))).all())


async def list_queue(session: AsyncSession, book_id: uuid.UUID | None) -> dict:
    base = select(Job, Chapter).outerjoin(Chapter, Chapter.id == Job.chapter_id)
    if book_id is not None:
        base = base.where(Job.book_id == book_id)
    active = (await session.execute(base.where(Job.status.in_(ACTIVE_JOB_STATUSES)).order_by(Job.position))).all()
    recent = (
        await session.execute(
            base.where(Job.status.in_(FINISHED_JOB_STATUSES)).order_by(Job.finished_at.desc().nulls_last()).limit(RECENT_LIMIT)
        )
    ).all()

    elsewhere = None
    if book_id is not None:
        row = (
            await session.execute(
                select(Job.book_id, Book.title_vi, Book.title_zh)
                .join(Book, Book.id == Job.book_id)
                .where(Job.status == "running", Job.book_id != book_id, Job.kind.in_(QUEUE_KINDS))
                .limit(1)
            )
        ).first()
        if row:
            elsewhere = {"book_id": str(row[0]), "title": row[1] or row[2]}

    durations = [
        (j.finished_at - j.started_at).total_seconds()
        for j, _ in recent
        if j.status == "done" and j.kind in QUEUE_KINDS and j.started_at and j.finished_at
    ]
    pending = sum(1 for j, _ in active if j.kind in QUEUE_KINDS)
    eta = int(sum(durations) / len(durations) * pending) if durations and pending else None
    return {
        "paused": await _paused_map(session),
        "paused_reason": dict((await session.execute(select(WorkerState.engine, WorkerState.paused_reason))).all()),
        "active": [job_view(j, c) for j, c in active],
        "recent": [job_view(j, c) for j, c in recent],
        "running_elsewhere": elsewhere,
        "eta_seconds": eta,
    }


async def set_paused(session: AsyncSession, engine: str, paused: bool) -> dict:
    await session.execute(update(WorkerState).where(WorkerState.engine == engine).values(paused=paused, paused_reason=None, updated_at=utcnow()))
    if not paused:
        resumed = (
            await session.scalars(
                update(Job).where(Job.engine == engine, Job.status == "paused").values(status="queued").returning(Job)
            )
        ).all()
        for job in resumed:
            await events.emit_job(session, job)
    await logs.write_log(
        session, level="info", source="system", message=("Tạm dừng" if paused else "Tiếp tục") + f" hàng đợi {engine}"
    )
    return {"engine": engine, "paused": paused}


async def pause_engine(session: AsyncSession, engine: str, *, reason: str | None, park_queued: bool) -> list[Job]:
    """Pool tự tạm dừng (BR-8.13, BR-8.14, G5). park_queued: job đang chờ chuyển sang `paused` (401 / 402)."""
    await session.execute(update(WorkerState).where(WorkerState.engine == engine)
                          .values(paused=True, paused_reason=reason, updated_at=utcnow()))
    parked: list[Job] = []
    if park_queued:
        parked = list((await session.scalars(
            update(Job).where(Job.engine == engine, Job.status == "queued").values(status="paused").returning(Job)
        )).all())
        for job in parked:
            await events.emit_job(session, job)
    await events.publish(session, "job.updated", None, {"engine": engine, "paused": True, "reason": reason})
    return parked


async def _get_job(session: AsyncSession, job_id: uuid.UUID, *, lock: bool = False) -> Job:
    stmt = select(Job).where(Job.id == job_id)
    if lock:
        stmt = stmt.with_for_update()
    job = (await session.scalars(stmt)).first()
    if job is None:
        raise AppError("JOB_NOT_FOUND", "Không tìm thấy job", 404)
    return job


async def move_job(session: AsyncSession, job_id: uuid.UUID, position: int) -> dict:
    job = await _get_job(session, job_id, lock=True)
    if job.status not in ("queued", "paused"):
        raise AppError("JOB_NOT_MOVABLE", "Chỉ đổi thứ tự được job đang chờ", 409)
    waiting = (
        await session.scalars(
            select(Job)
            .where(Job.engine == job.engine, Job.status.in_(("queued", "paused")))
            .order_by(Job.position)
            .with_for_update()
        )
    ).all()
    slots = [j.position for j in waiting]  # giữ nguyên tập vị trí, chỉ hoán đổi ai đứng ở đâu
    others = [j for j in waiting if j.id != job.id]
    target = max(0, min(position, len(others)))
    others.insert(target, job)
    for j, pos in zip(others, slots):
        j.position = pos
    await session.flush()
    await events.emit_job(session, job)
    chapter = await session.get(Chapter, job.chapter_id) if job.chapter_id else None
    return job_view(job, chapter)


async def _restore_chapter(session: AsyncSession, job: Job) -> None:
    if job.chapter_id is None or job.kind == "review":  # job soát không đổi trạng thái chương
        return
    chapter = await session.get(Chapter, job.chapter_id)
    if chapter is not None:
        chapter.status = job.prev_chapter_status or "todo"
        await events.emit_chapter(session, chapter)


async def cancel_job(session: AsyncSession, job_id: uuid.UUID) -> dict:
    job = await _get_job(session, job_id, lock=True)
    if job.status in FINISHED_JOB_STATUSES:
        raise AppError("JOB_FINISHED", "Job đã kết thúc", 409)
    was_running = job.status == "running"
    job.status = "cancelled"
    job.finished_at = utcnow()
    if not was_running:  # job đang chạy: worker tự dọn và trả chương về như cũ
        await session.execute(delete(JobSegment).where(JobSegment.job_id == job.id))
        await _restore_chapter(session, job)
    await events.emit_job(session, job)
    chapter = await session.get(Chapter, job.chapter_id) if job.chapter_id else None
    await logs.write_log(
        session,
        level="info",
        source="system",
        book_id=job.book_id,
        job_id=job.id,
        chapter_id=job.chapter_id,
        chapter_no=chapter.no if chapter else None,
        message=f"Huỷ job chương {chapter.no if chapter else '?'}",
    )
    await events.emit_book_stats(session, job.book_id)
    return job_view(job, chapter)


async def has_other_active_job(session: AsyncSession, chapter_id: uuid.UUID, exclude_job_id: uuid.UUID) -> bool:
    """Chương còn job khác (đang chờ / chạy / tạm dừng) thì không được trả trạng thái chương về cũ."""
    found = await session.scalar(
        select(Job.id)
        .where(Job.chapter_id == chapter_id, Job.id != exclude_job_id, Job.status.in_(ACTIVE_JOB_STATUSES),
               Job.kind.in_(TRANSLATE_KINDS))
        .limit(1)
    )
    return found is not None


async def recover_interrupted(session: AsyncSession, engine: str) -> int:
    """NFR-3: job còn `running` khi worker / pool khởi động là job bị ngắt giữa chừng.

    Mỗi bộ chạy chỉ khôi phục loại job của mình. Job soát không đổi trạng thái chương.
    Job dịch đã bị huỷ lúc worker không chạy thì chưa ai dọn: xoá phần dịch dở và trả chương về trạng thái cũ."""
    kinds = ENGINE_KINDS[engine]
    jobs = (
        await session.scalars(
            update(Job)
            .where(Job.engine == engine, Job.kind.in_(kinds), Job.status == "running")
            .values(status="queued")
            .returning(Job)
        )
    ).all()
    for job in jobs:
        await events.emit_job(session, job)
        if job.chapter_id and job.kind in TRANSLATE_KINDS:
            chapter = await session.get(Chapter, job.chapter_id)
            if chapter is not None:
                chapter.status = "queued"
                await events.emit_chapter(session, chapter)
    if jobs:
        await logs.write_log(
            session, level="warn", source="system", message=f"Đưa {len(jobs)} job {engine} bị ngắt giữa chừng về hàng đợi"
        )

    cancelled = (
        await session.scalars(
            select(Job)
            .join(Chapter, Chapter.id == Job.chapter_id)
            .where(Job.engine == engine, Job.kind.in_(TRANSLATE_KINDS), Job.status == "cancelled",
                   Chapter.status == "translating")
            .with_for_update(of=Job)
        )
    ).all()
    for job in cancelled:
        await session.execute(delete(JobSegment).where(JobSegment.job_id == job.id))
        chapter = await session.get(Chapter, job.chapter_id)
        if chapter is not None and chapter.status == "translating" and not await has_other_active_job(
            session, chapter.id, job.id
        ):
            chapter.status = job.prev_chapter_status or "todo"
            await events.emit_chapter(session, chapter)
    if cancelled:
        await logs.write_log(
            session, level="info", source="system", message=f"Dọn {len(cancelled)} job đã huỷ lúc worker không chạy"
        )
    return len(jobs)
