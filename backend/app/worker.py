import asyncio
import hashlib
import logging
import signal
import time
import traceback
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import anyio
from sqlalchemy import delete, func, insert, select, update

from app.core.compare import carried_versions, engine_column
from app.core.glossary import GlossaryIndex
from app.core.jobs import LINES_PER_STEP, chapter_status_after, count_translatable, is_out_of_memory, plan_steps, progress_pct
from app.core.pipeline import ChapterResult, translate_segments
from app.core.segments import SegmentPlan, plan_segments
from app.core.textio import decode_source
from app.core.translator import Translator
from app.honorific.engine import SegOut, Stats, apply_chapter, classify
from app.db import get_sessionmaker
from app.models import Book, Chapter, ChapterRevision, Job, JobSegment, Segment, WorkerState, utcnow
from app.services import events, logs, queue, versions
from app.services import glossary as glossary_service
from app.services import review as review_service
from app.services.honorific import honorific_items
from app.services.revisions import snapshot_rows
from app.services.paths import chapter_source_path

log = logging.getLogger("worker")

MAX_OOM_RETRIES = 2
PROGRESS_MIN_INTERVAL = 0.25  # BR-3.12: tối đa 4 lần mỗi giây
IDLE_POLL_SECONDS = 1.0
MAINTENANCE_EVERY = 3600
ERROR_BACKOFF_START = 1.0
ERROR_BACKOFF_MAX = 30.0


class JobInterrupted(Exception):
    def __init__(self, reason: str):  # "paused" | "cancelled" | "stopped"
        super().__init__(reason)
        self.reason = reason


@dataclass
class OomBudget:
    """BR-3.11: số lần giảm batch vì hết bộ nhớ được tính cho cả job, không phải từng bước."""

    retries: int = 0


@dataclass
class JobContext:
    job_id: uuid.UUID
    book_id: uuid.UUID
    chapter_id: uuid.UUID
    chapter_no: int
    run_config: dict
    options: dict
    prev_status: str | None
    genre: str = "other"
    register_override: str | None = None


class Worker:
    def __init__(
        self,
        translator_factory: Callable[[], Translator],
        *,
        engine: str = "ct2",
        lines_per_step: int = LINES_PER_STEP,
        on_step: Callable[[int], Awaitable[None]] | None = None,
    ):
        self._factory = translator_factory
        self._translator: Translator | None = None
        self.engine = engine
        self.lines_per_step = lines_per_step
        self.on_step = on_step
        self._stop = asyncio.Event()
        self._last_progress = 0.0
        self._sessions = get_sessionmaker()

    # ---------- vòng đời ----------

    async def _load_translator(self) -> Translator:
        if self._translator is None:
            t0 = time.perf_counter()
            self._translator = await anyio.to_thread.run_sync(self._factory)
            elapsed = time.perf_counter() - t0
            async with self._sessions() as s:
                await logs.write_log(
                    s,
                    level="info",
                    source="system",
                    provider=self.engine,
                    model=self._translator.model_id,
                    message=f"Nạp model {self._translator.model_id} ({elapsed:.1f}s)",
                    latency_ms=int(elapsed * 1000),
                )
                await s.commit()
        return self._translator

    async def startup(self) -> None:
        async with self._sessions() as s:
            await logs.ensure_partitions(s)
            await queue.recover_interrupted(s, self.engine)
            await s.commit()
        await self._load_translator()

    async def maintenance(self) -> None:
        async with self._sessions() as s:
            await logs.ensure_partitions(s)
            await logs.purge_old_logs(s)
            await s.commit()

    def request_stop(self) -> None:
        self._stop.set()

    async def _sleep_or_stop(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), delay)
        except TimeoutError:
            pass

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        if stop is not None:
            self._stop = stop
        backoff = ERROR_BACKOFF_START
        while not self._stop.is_set():  # khởi động lại tới khi thành công (DB có thể chưa sẵn sàng)
            try:
                await self.startup()
                break
            except Exception:
                log.exception("Worker khởi động lỗi, thử lại sau %.1fs", backoff)
                await self._sleep_or_stop(backoff)
                backoff = min(backoff * 2, ERROR_BACKOFF_MAX)
        backoff = ERROR_BACKOFF_START
        last_maintenance = 0.0
        while not self._stop.is_set():
            try:
                if time.monotonic() - last_maintenance > MAINTENANCE_EVERY:
                    await self.maintenance()
                    last_maintenance = time.monotonic()
                if not await self.run_once():
                    await self._sleep_or_stop(IDLE_POLL_SECONDS)
                backoff = ERROR_BACKOFF_START
            except Exception:  # một lỗi không được làm chết cả vòng lặp
                log.exception("Vòng lặp worker lỗi, thử lại sau %.1fs", backoff)
                await self._sleep_or_stop(backoff)
                backoff = min(backoff * 2, ERROR_BACKOFF_MAX)

    async def run_once(self) -> bool:
        job_id = await self.claim_next()
        if job_id is None:
            return False
        await self.run_job(job_id)
        return True

    # ---------- lấy job ----------

    async def claim_next(self) -> uuid.UUID | None:
        async with self._sessions() as s:
            paused = await s.scalar(select(WorkerState.paused).where(WorkerState.engine == self.engine))
            if paused:
                return None
            job = (
                await s.scalars(
                    select(Job)
                    .where(Job.engine == self.engine, Job.status == "queued", Job.kind.in_(("translate", "retranslate")))
                    .order_by(Job.position, Job.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).first()
            if job is None:
                return None
            job.status = "running"
            job.attempts += 1
            if job.started_at is None:
                job.started_at = utcnow()
            chapter = await s.get(Chapter, job.chapter_id)
            chapter.status = "translating"
            await events.emit_job(s, job)
            await events.emit_chapter(s, chapter)
            await events.emit_book_stats(s, job.book_id)
            await s.commit()
            return job.id

    # ---------- chạy job ----------

    async def run_job(self, job_id: uuid.UUID) -> None:
        started = time.perf_counter()
        try:
            async with self._sessions() as s:
                job = await s.get(Job, job_id)
                chapter = await s.get(Chapter, job.chapter_id) if job and job.chapter_id else None
                book = await s.get(Book, job.book_id) if job else None
                if job is None or chapter is None or book is None:  # chương / truyện vừa bị xoá
                    return
                ctx = JobContext(job.id, job.book_id, chapter.id, chapter.no, job.run_config, job.options or {}, job.prev_chapter_status,
                                 book.genre, chapter.register_override)
                source = chapter_source_path(book.slug, chapter.source_file or f"{chapter.no:04d}.txt")
                previous_hash = job.source_hash
        except Exception:
            log.exception("Không nạp được ngữ cảnh job %s", job_id)
            return
        try:
            translator = await self._load_translator()
            async with self._sessions() as s:
                index = await glossary_service.load_index(s, ctx.book_id)  # chụp một lần cho cả lần chạy
            data = await anyio.to_thread.run_sync(source.read_bytes)
            source_hash = hashlib.sha256(data).hexdigest()
            plans = plan_segments(decode_source(data).text)
            done = await self._prepare_staging(ctx, source_hash, previous_hash)
            total = count_translatable(plans)
            beam = int(ctx.run_config.get("beam", 2))
            batch_size = int(ctx.run_config.get("batch", {}).get("size", 32))
            oom = OomBudget()
            for step_no, step in enumerate(plan_steps(plans, done, self.lines_per_step), start=1):
                await self._check_interrupt(ctx)
                result, batch_size = await self._translate_step(ctx, translator, step, beam, batch_size, oom, index)
                done |= {p.idx for p in step}
                await self._save_step(ctx, result, len(done), total)
                if self.on_step is not None:
                    await self.on_step(step_no)
            await self._finish(ctx, translator, plans, total, beam, batch_size, started, index)
        except JobInterrupted as e:
            await self._safely(self._interrupted(ctx, e.reason), ctx)
        except Exception as e:
            await self._safely(self._fail(ctx, e, started), ctx)

    async def _safely(self, coro: Awaitable[None], ctx: JobContext) -> None:
        """Dọn dẹp job lỗi cũng có thể lỗi (mất DB): ghi log, không ném ra ngoài run_job."""
        try:
            await coro
        except Exception:
            log.exception("Không dọn được job %s", ctx.job_id)

    async def _prepare_staging(self, ctx: JobContext, source_hash: str, previous_hash: str | None) -> set[int]:
        async with self._sessions() as s:
            if previous_hash and previous_hash != source_hash:  # bản gốc đã bị thay: làm lại từ đầu
                await s.execute(delete(JobSegment).where(JobSegment.job_id == ctx.job_id))
            await s.execute(update(Job).where(Job.id == ctx.job_id).values(source_hash=source_hash))
            done = set((await s.scalars(select(JobSegment.idx).where(JobSegment.job_id == ctx.job_id))).all())
            await s.commit()
        return done

    async def _check_interrupt(self, ctx: JobContext) -> None:
        async with self._sessions() as s:
            status = await s.scalar(select(Job.status).where(Job.id == ctx.job_id))
            paused = await s.scalar(select(WorkerState.paused).where(WorkerState.engine == self.engine))
        if status in (None, "cancelled"):
            raise JobInterrupted("cancelled")
        if self._stop.is_set():
            raise JobInterrupted("stopped")
        if paused:
            raise JobInterrupted("paused")

    async def _translate_step(
        self, ctx: JobContext, translator: Translator, step: list[SegmentPlan], beam: int, batch_size: int,
        oom: OomBudget, glossary: GlossaryIndex,
    ) -> tuple[ChapterResult, int]:
        while True:
            size = batch_size
            try:
                result = await anyio.to_thread.run_sync(
                    lambda: translate_segments(step, translator, beam=beam, batch_size=size, glossary=glossary)
                )
                return result, batch_size
            except Exception as e:
                if not is_out_of_memory(e) or oom.retries >= MAX_OOM_RETRIES or batch_size <= 1:
                    raise
                oom.retries += 1
                new_size = max(1, batch_size // 2)
                async with self._sessions() as s:
                    await logs.write_log(
                        s,
                        level="warn",
                        source="translate",
                        book_id=ctx.book_id,
                        chapter_id=ctx.chapter_id,
                        chapter_no=ctx.chapter_no,
                        job_id=ctx.job_id,
                        provider=self.engine,
                        message=f"Chương {ctx.chapter_no} · hết bộ nhớ, giảm batch {batch_size} → {new_size}",
                    )
                    await s.commit()
                batch_size = new_size

    async def _save_step(self, ctx: JobContext, result: ChapterResult, done: int, total: int) -> None:
        pct = progress_pct(done, total)
        async with self._sessions() as s:
            s.add_all(JobSegment(job_id=ctx.job_id, idx=r.idx, dst=r.dst, flags=r.flags, glossary_hits=r.glossary_hits) for r in result.segments)
            await s.execute(
                update(Job)
                .where(Job.id == ctx.job_id)
                .values(
                    progress=pct,
                    tokens_in=Job.tokens_in + result.tokens_in,
                    tokens_out=Job.tokens_out + result.tokens_out,
                    updated_at=utcnow(),
                )
            )
            now = time.monotonic()
            if now - self._last_progress >= PROGRESS_MIN_INTERVAL:
                self._last_progress = now
                job = await s.scalar(select(Job).where(Job.id == ctx.job_id))  # đọc lại để payload đủ khoá như emit_job
                if job is not None:
                    await events.emit_job(s, job)
            await s.commit()

    async def _finish(
        self,
        ctx: JobContext,
        translator: Translator,
        plans: list[SegmentPlan],
        total: int,
        beam: int,
        batch_size: int,
        started: float,
        index: GlossaryIndex,
    ) -> None:
        async with self._sessions() as s:
            job = (await s.scalars(select(Job).where(Job.id == ctx.job_id).with_for_update())).first()
            if job is None or job.status != "running":  # bị huỷ đúng lúc này: không ghi đè (Review Focus 2)
                await s.rollback()
                raise_reason = "cancelled"
            else:
                raise_reason = None
                await self._write_result(s, ctx, job, translator, plans, total, beam, batch_size, started, index)
                await s.commit()
        if raise_reason:
            await self._interrupted(ctx, raise_reason)

    async def _write_result(self, s, ctx, job, translator, plans, total, beam, batch_size, started, index) -> None:
        staged = {r.idx: r for r in (await s.scalars(select(JobSegment).where(JobSegment.job_id == ctx.job_id))).all()}
        keep: dict[int, Segment] = {}
        if ctx.options.get("keep_manual_edits"):
            by_idx = {p.idx: p for p in plans}
            existing = (await s.scalars(select(Segment).where(Segment.chapter_id == ctx.chapter_id, Segment.edited))).all()
            keep = {seg.idx: seg for seg in existing if seg.idx in by_idx and by_idx[seg.idx].src == seg.src}
        kept_dst = {idx: seg.dst for idx, seg in keep.items()}

        text_plans = [p for p in plans if not p.is_meta]
        chapter = await s.get(Chapter, ctx.chapter_id, populate_existing=True)
        if self.engine == "ct2":  # chỉ chuẩn hoá đầu ra HachimiMT
            cfg = (ctx.run_config or {}).get("honorific") or {}
            override = chapter.register_override  # đọc lại: người dùng có thể đổi giữa lúc job chạy
            if override:
                route, score = override, None
            else:
                route, score = classify("\n".join(p.src for p in plans), ctx.genre)
            outs, h_stats = apply_chapter(
                honorific_items([(p.src, staged[p.idx].dst) for p in text_plans], index), route, cfg)
            honor = {p.idx: o for p, o in zip(text_plans, outs)}
        else:
            route, score, h_stats = None, None, Stats()
            honor = {p.idx: SegOut(staged[p.idx].dst, []) for p in text_plans}

        prev = await versions.previous_versions(s, ctx.chapter_id)  # BR-6.10: đọc trước khi xoá
        column = engine_column(translator.model_id)  # cùng quy tắc với chapter.model_id (BR-6.12)
        await s.execute(delete(Segment).where(Segment.chapter_id == ctx.chapter_id))
        await review_service.supersede_pending(s, ctx.chapter_id)
        rows = []
        for p in plans:
            if p.is_meta:
                rows.append({"chapter_id": ctx.chapter_id, "idx": p.idx, "src": p.src, "is_meta": True,
                             "dst": p.src, "dst_machine": p.src, "dst_model_raw": p.src, "honorific_edits": [],
                             "edited": False, "flags": [], "glossary_hits": [], "dst_mt": None, "dst_ai": None})
                continue
            r = staged[p.idx]
            edited = p.idx in kept_dst
            h = honor[p.idx]
            flags = list(r.flags) + (["honorific_rewritten"] if h.edits and not edited else [])
            rows.append({"chapter_id": ctx.chapter_id, "idx": p.idx, "src": p.src, "is_meta": False,
                         "dst": kept_dst[p.idx] if edited else h.dst, "dst_machine": h.dst, "dst_model_raw": r.dst,
                         "honorific_edits": h.edits, "edited": edited, "flags": flags,
                         "glossary_hits": r.glossary_hits, **carried_versions(column, p.src, h.dst, prev.get(p.idx))})
        if rows:
            await s.execute(insert(Segment), rows)

        status = chapter_status_after(row["flags"] for row in rows)
        now = utcnow()
        chapter.status = status
        chapter.model_id = translator.model_id
        chapter.translated_at = now
        chapter.error = None
        chapter.register_route, chapter.register_score = route, score
        s.add(ChapterRevision(chapter_id=ctx.chapter_id, kind="machine", model_id=translator.model_id,
                              run_config=job.run_config, segments_changed=total,
                              snapshot=snapshot_rows(rows), changed_idx=[]))
        job.status, job.progress, job.finished_at, job.error = "done", 100, now, None
        await s.execute(delete(JobSegment).where(JobSegment.job_id == ctx.job_id))

        flag_counts: dict[str, int] = {}
        for row in rows:
            for f in row["flags"]:
                flag_counts[f] = flag_counts.get(f, 0) + 1
        flagged = sum(1 for row in rows if any(f != "honorific_rewritten" for f in row["flags"]))
        message = f"Chương {ctx.chapter_no} · {total} câu"
        if flagged:
            message += f" · {flagged} câu có cờ, cần soát" if status == "needs_review" else f" · {flagged} câu có cờ"
        await logs.write_log(
            s,
            level="warn" if status == "needs_review" else "info",
            source="translate",
            book_id=ctx.book_id,
            chapter_id=ctx.chapter_id,
            chapter_no=ctx.chapter_no,
            job_id=ctx.job_id,
            provider=self.engine,
            model=translator.model_id,
            message=message,
            tokens_in=job.tokens_in,
            tokens_out=job.tokens_out,
            latency_ms=int((time.perf_counter() - started) * 1000),
            params={"beam": beam, "batch": batch_size, "chunk_mode": ctx.run_config.get("chunk_mode")},
            detail={"honorific": {"route": route, "score": score, **h_stats.to_json()},
                    "segments": {"total": total, "flagged": flagged, "flags": flag_counts,
                                 "kept_manual": len(kept_dst),
                                 "glossary_hits": sum(1 for row in rows if row["glossary_hits"]),
                                 "retried": flag_counts.get("retried", 0),
                                 "placeholder_lost": flag_counts.get("placeholder_lost", 0)}},
        )
        if (job.run_config.get("review") or {}).get("auto_after_ct2"):  # BR-8.23
            await queue.enqueue_review(s, await s.get(Book, ctx.book_id), [chapter])
        await events.emit_job(s, job)
        await events.emit_chapter(s, chapter)
        await events.emit_book_stats(s, ctx.book_id)

    async def _interrupted(self, ctx: JobContext, reason: str) -> None:
        async with self._sessions() as s:
            job = (await s.scalars(select(Job).where(Job.id == ctx.job_id).with_for_update())).first()
            chapter = await s.get(Chapter, ctx.chapter_id)
            if job is None or job.status == "cancelled":  # huỷ thắng tạm dừng / dừng worker
                reason = "cancelled"
            if reason == "cancelled":
                await s.execute(delete(JobSegment).where(JobSegment.job_id == ctx.job_id))
                if chapter is not None and not await queue.has_other_active_job(s, ctx.chapter_id, ctx.job_id):
                    chapter.status = ctx.prev_status or "todo"
                message = f"Đã huỷ chương {ctx.chapter_no}, bỏ phần dịch dở"
            else:
                if job is not None and job.status == "running":
                    job.status = "paused" if reason == "paused" else "queued"
                if chapter is not None:
                    chapter.status = "queued"
                message = f"Tạm dừng chương {ctx.chapter_no}" if reason == "paused" else f"Dừng worker, chương {ctx.chapter_no} quay lại hàng đợi"
            await logs.write_log(s, level="info", source="system", book_id=ctx.book_id, chapter_id=ctx.chapter_id,
                                 chapter_no=ctx.chapter_no, job_id=ctx.job_id, message=message)
            if job is not None:
                await events.emit_job(s, job)
            if chapter is not None:
                await events.emit_chapter(s, chapter)
            await events.emit_book_stats(s, ctx.book_id)
            await s.commit()

    async def _fail(self, ctx: JobContext, exc: Exception, started: float) -> None:
        log.exception("Job %s lỗi", ctx.job_id)
        message = f"{type(exc).__name__}: {exc}"
        async with self._sessions() as s:
            job = await s.get(Job, ctx.job_id)
            chapter = await s.get(Chapter, ctx.chapter_id)
            if job is not None and job.status == "cancelled":  # bị huỷ rồi thì không ghi lỗi đè lên
                await s.rollback()
                await self._interrupted(ctx, "cancelled")
                return
            await s.execute(delete(JobSegment).where(JobSegment.job_id == ctx.job_id))
            if job is not None:
                job.status, job.error, job.finished_at = "failed", message[:1000], utcnow()
            if chapter is not None:
                chapter.status, chapter.error = "error", message[:1000]
            await logs.write_log(
                s,
                level="error",
                source="translate",
                book_id=ctx.book_id,
                chapter_id=ctx.chapter_id,
                chapter_no=ctx.chapter_no,
                job_id=ctx.job_id,
                provider=self.engine,
                message=f"Chương {ctx.chapter_no} · lỗi: {message}",
                latency_ms=int((time.perf_counter() - started) * 1000),
                detail={"error": {"type": type(exc).__name__, "message": str(exc),
                                  "stack": "".join(traceback.format_exception(exc))}},
            )
            if job is not None:
                await events.emit_job(s, job)
            if chapter is not None:
                await events.emit_chapter(s, chapter)
            await events.emit_book_stats(s, ctx.book_id)
            await s.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from app.deepseek.client import DeepSeekClient
    from app.deepseek.pool import DeepSeekPool
    from app.services.translators import default_translator_factory

    async def runner() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        # NFR-4: worker CPU dịch tuần tự, pool API song song; chung một process, chung HachimiMT (dịch dòng DeepSeek bỏ sót)
        await asyncio.gather(
            Worker(default_translator_factory).run_forever(stop),
            DeepSeekPool(DeepSeekClient.from_settings, translator_factory=default_translator_factory).run_forever(stop),
        )

    asyncio.run(runner())


if __name__ == "__main__":
    main()
