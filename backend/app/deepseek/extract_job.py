"""Job `ai_extract` trong pool DeepSeek (spec 04 BR-4.9, BR-4.10; spec 08 BR-8.17).

Job không gắn với một chương (`chapter_id = NULL`): danh sách chương nằm ở `options.chapter_ids`. Trích lỗi không bao
giờ đổi trạng thái chương."""
import logging
import time
import traceback
import uuid

from sqlalchemy import select, update

from app.core.jobs import progress_pct
from app.core.run_config import deepseek_model
from app.deepseek import extract as core
from app.deepseek.client import DeepSeekAuthError
from app.deepseek.pool import ENGINE, PoolJob
from app.models import Book, Job, utcnow
from app.services import ai_cost, events, extraction, logs, queue
from app.worker import JobInterrupted

log = logging.getLogger("deepseek.extract")


class ExtractRunner:
    def __init__(self, pool):
        self.pool = pool

    async def run(self, job_id: uuid.UUID) -> None:
        try:
            await self._run(job_id)
        except Exception as exc:  # không để job kẹt ở running
            log.exception("Job trích glossary %s lỗi", job_id)
            await self._mark_failed(job_id, exc)

    async def _run(self, job_id: uuid.UUID) -> None:
        started = time.perf_counter()
        async with self.pool._sessions() as s:
            job = await s.get(Job, job_id)
            book = await s.get(Book, job.book_id) if job else None
            if job is None or book is None:
                return
            opts = job.options or {}
            model_id = opts.get("model") or deepseek_model(job.run_config or {})
            p = PoolJob(job.id, job.book_id, None, None, "ai_extract", model_id, job.run_config or {}, opts, None)
            chapters = await extraction.load_chapters(s, book, [uuid.UUID(c) for c in opts.get("chapter_ids") or []])
            known, decided = await extraction.known_sources(s, book.id)
            model = await ai_cost.get_model(s, model_id)

        async def progress(done: int, total: int) -> None:
            async with self.pool._sessions() as s2:
                row = (await s2.scalars(update(Job).where(Job.id == p.job_id, Job.status == "running")
                                        .values(progress=min(99, progress_pct(done, total)), updated_at=utcnow())
                                        .returning(Job))).first()
                if row is not None:
                    await events.emit_job(s2, row)
                await s2.commit()

        try:
            outcome = await extraction.run_batches(
                self.pool._client_factory(), model_id=model_id, model=model, chapters=chapters, known=known,
                rejected=decided, categories=list(opts.get("categories") or core.DEFAULT_CATEGORIES),
                book_id=p.book_id, job_id=p.job_id,
                check=lambda: self.pool._check_interrupt(p),
                on_usage=lambda completion, est: self.pool._check_ratio(p, "extract", completion, est),  # G5: trích > 2,0
                on_progress=progress)
        except extraction.ExtractFailed as failed:
            await self._stopped(p, failed, started)
            return
        await self._close(p, outcome, status="done", started=started)

    async def _stopped(self, p: PoolJob, failed: extraction.ExtractFailed, started: float) -> None:
        cause = failed.cause
        if isinstance(cause, JobInterrupted):
            if cause.reason == "cancelled":
                return  # job đã cancelled: không lưu gì
            await self._close(p, failed.outcome, status="paused" if cause.reason == "paused" else "queued", started=started)
            return
        if isinstance(cause, DeepSeekAuthError):  # BR-8.13; AC-4.6
            async with self.pool._sessions() as s:
                await queue.pause_engine(s, ENGINE, reason="auth" if cause.status is not None else "no_key", park_queued=True)
                await s.commit()
            await self._close(p, failed.outcome, status="paused", started=started, error=cause)
            return
        await self._close(p, failed.outcome, status="failed", started=started, error=cause)  # AC-4.9

    async def _close(self, p: PoolJob, outcome: extraction.ExtractOutcome, *, status: str, started: float,
                     error: BaseException | None = None) -> None:
        """Lưu đề xuất của các lô đã xong và đóng job trong cùng một transaction."""
        async with self.pool._sessions() as s:
            job = (await s.scalars(select(Job).where(Job.id == p.job_id).with_for_update())).first()
            if job is None or job.status != "running":  # bị huỷ đúng lúc này
                await s.rollback()
                return
            book = await s.get(Book, p.book_id)
            proposals = outcome.proposals()
            counts = await extraction.count_in_book(s, book, [x.src for x in proposals])
            saved = await extraction.store_suggestions(s, p.book_id, proposals, counts, model_id=p.model_id)
            await extraction.mark_scanned(s, outcome.scanned)
            job.tokens_in += outcome.usage.prompt_tokens
            job.tokens_out += outcome.usage.completion_tokens
            done = f"{outcome.batches_done}/{outcome.batches_total} lô"
            source = "glossary"
            if status == "done":
                job.status, job.progress, job.finished_at, job.error = "done", 100, utcnow(), None
                level, message = "info", f"Trích glossary xong · {done} · {saved['stored']} đề xuất chờ duyệt"
            elif status == "failed":
                job.status, job.finished_at, job.error = "failed", utcnow(), f"{type(error).__name__}: {error}"[:1000]
                level = "error"
                message = (f"Trích glossary lỗi ở lô {outcome.batches_done + 1}/{outcome.batches_total}: {error}. "
                           f"Giữ {saved['stored']} đề xuất từ {outcome.batches_done} lô đã xong")
            elif error is not None:  # 401 / 402 / chưa có key
                job.status, level, source = status, "error", "system"
                message = f"{error}. Đã tạm dừng pool DeepSeek, các job DeepSeek đang chờ chuyển sang tạm dừng."
            else:
                job.status, level = status, "info"
                message = f"Tạm dừng job trích glossary sau {done}, giữ {saved['stored']} đề xuất"
            detail = {"dropped": outcome.drops.view(), **saved,
                      "batches": {"done": outcome.batches_done, "total": outcome.batches_total}}
            if error is not None:
                detail["error"] = {"type": type(error).__name__, "status": getattr(error, "status", None),
                                   "message": str(error), "stack": "".join(traceback.format_exception(error))}
            await logs.write_log(s, level=level, source=source, book_id=p.book_id, job_id=p.job_id, provider=ENGINE,
                                 model=p.model_id, message=message, latency_ms=int((time.perf_counter() - started) * 1000),
                                 detail=detail)
            await events.emit_job(s, job)
            await events.publish(s, "glossary.suggestions", p.book_id, {"stored": saved["stored"]})
            await s.commit()

    async def _mark_failed(self, job_id: uuid.UUID, exc: Exception) -> None:
        try:
            async with self.pool._sessions() as s:
                job = await s.get(Job, job_id)
                if job is None or job.status != "running":
                    return
                job.status, job.finished_at, job.error = "failed", utcnow(), f"{type(exc).__name__}: {exc}"[:1000]
                await logs.write_log(s, level="error", source="glossary", book_id=job.book_id, job_id=job.id,
                                     provider=ENGINE, message=f"Job trích glossary lỗi: {exc}")
                await events.emit_job(s, job)
                await s.commit()
        except Exception:
            log.exception("Không đánh dấu được job trích glossary %s là lỗi", job_id)
