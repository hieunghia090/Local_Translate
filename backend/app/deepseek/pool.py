"""Pool API DeepSeek (BR-8.15, BR-8.16). Chạy cùng process với worker CPU; mỗi job là một asyncio task.

Kết quả từng phần ghi vào job_segments (tạm dừng / tiếp tục được). Xong cả chương mới chép sang segments như đường
HachimiMT, nhưng không chạy lớp xưng hô (BR-8.2)."""
import asyncio
import logging
import math
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import anyio
from sqlalchemy import delete, func, insert, or_, select, update

from app.core.compare import AI_COLUMN, carried_versions
from app.core.jobs import chapter_status_after, progress_pct
from app.core.pipeline import translate_segments
from app.core.run_config import deepseek_model
from app.core.translator import Translator
from app.db import get_sessionmaker
from app.deepseek import guards, prompts
from app.deepseek.catalog import FALLBACK_CONTEXT_WINDOW, FALLBACK_MAX_OUTPUT
from app.deepseek.client import (
    MAX_ATTEMPTS, Completion, DeepSeekAuthError, DeepSeekClient, DeepSeekError, DeepSeekModelUnavailable,
    DeepSeekRateLimited, DeepSeekTruncated, OutputRejected, RateBudget, Usage, build_request,
)
from app.deepseek.context import TranslateContext, load_review_context, load_translate_context
from app.deepseek.extract import DEFAULT_CATEGORIES
from app.deepseek.estimate import (  # noqa: F401  (OUT_HEADROOM, SAFE_OUT_RATIO: test và pool dùng chung)
    OUT_HEADROOM, REVIEW_OUT_SHARE, SAFE_OUT_RATIO, Coefficients, output_tokens_est, part_token_budget,
    prompt_tokens_est, review_max_tokens, text_tokens,
)
from app.models import (
    Book, Chapter, ChapterNote, ChapterRevision, GlossaryTerm, Job, JobSegment, Segment, WorkerState, utcnow,
)
from app.services import ai_cost, events, logs, queue, versions
from app.services import review as review_service
from app.services.revisions import snapshot_rows
from app.worker import JobInterrupted

log = logging.getLogger("deepseek")

ENGINE = "deepseek"
POOL_KINDS = ("translate", "retranslate", "review", "ai_extract")
_LOG_SOURCE = {"review": "review", "ai_extract": "glossary"}
DEFAULT_CONCURRENCY = 3
MAX_CONCURRENCY = 8
MIN_MAX_TOKENS = 1024  # G5
ABNORMAL_LIMIT = 2  # G5: 2 request bất thường liên tiếp thì ngắt mạch
FAIL_LIMIT = 5  # BR-8.14
ALWAYS_SEND_AFTER_MISSES = 3  # spec 08 G4, BR-4.18
IDLE_POLL_SECONDS = 1.0
STOP_GRACE_SECONDS = 30.0
ERROR_BACKOFF_START = 1.0
ERROR_BACKOFF_MAX = 30.0


@dataclass
class PoolJob:
    job_id: uuid.UUID
    book_id: uuid.UUID
    chapter_id: uuid.UUID
    chapter_no: int
    kind: str
    model_id: str
    run_config: dict
    options: dict
    prev_status: str | None


@dataclass(frozen=True)
class RequestEst:
    """Ước tính của một body request (để ghi cùng token thật vào dòng log của request đó)."""

    tokens_in: int = 0
    tokens_out: int = 0
    text_tokens: int = 0
    overhead_tokens: int = 0


def keeps_staging(exc: Exception) -> bool:
    """Lỗi có thể thử lại (mạng, 5xx, quá tải 429, output bị từ chối): giữ phần đã dịch để lần thử lại không tốn tiền lại.
    Lỗi không thể thử lại (model, 400, bị cắt cụt, lỗi logic) thì xoá phần dịch dở."""
    if isinstance(exc, (DeepSeekModelUnavailable, DeepSeekTruncated)):
        return False
    if isinstance(exc, (DeepSeekRateLimited, OutputRejected)):
        return True
    return isinstance(exc, DeepSeekError) and exc.retryable


@dataclass
class RunStats:
    usage: Usage = field(default_factory=Usage)
    first_request: dict | None = None
    request_count: int = 0
    attempts: int = 0
    rate_waits: int = 0
    latency_ms: int = 0
    parts: int = 0
    resent: int = 0
    fallback: int = 0
    preamble: int = 0
    tokens_in_est: int = 0
    tokens_out_est: int = 0
    text_tokens: int = 0  # hệ số mặc định, để hiệu chỉnh (BR-8.24b)
    overhead_tokens: int = 0
    missed_terms: set[str] = field(default_factory=set)


class DeepSeekPool:
    def __init__(self, client_factory: Callable[[], DeepSeekClient], *,
                 translator_factory: Callable[[], Translator] | None = None):
        self._client_factory = client_factory
        self._translator_factory = translator_factory
        self._translator: Translator | None = None
        self._translator_lock = asyncio.Lock()
        self._sessions = get_sessionmaker()
        self._tasks: set[asyncio.Task] = set()
        self._stop = asyncio.Event()
        self._abnormal_streak = 0
        self._fail_streak = 0
        from app.deepseek.extract_job import ExtractRunner  # import muộn: extract_job import module này

        self._extract = ExtractRunner(self)

    # ---------- vòng đời ----------

    async def startup(self) -> None:
        async with self._sessions() as s:
            await queue.recover_interrupted(s, ENGINE)
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
        while not self._stop.is_set():  # DB có thể chưa sẵn sàng
            try:
                await self.startup()
                break
            except Exception:
                log.exception("Pool DeepSeek khởi động lỗi, thử lại sau %.1fs", backoff)
                await self._sleep_or_stop(backoff)
                backoff = min(backoff * 2, ERROR_BACKOFF_MAX)
        while not self._stop.is_set():
            try:
                await self.fill()
            except Exception:
                log.exception("Pool DeepSeek lỗi khi lấy job")
            if self._tasks:
                await asyncio.wait(set(self._tasks), timeout=IDLE_POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            else:
                await self._sleep_or_stop(IDLE_POLL_SECONDS)
        if self._tasks:  # job đang chạy thấy cờ dừng ở lần kiểm tra kế tiếp và quay về hàng đợi
            await asyncio.wait(set(self._tasks), timeout=STOP_GRACE_SECONDS)

    async def run_until_idle(self) -> None:
        """Chạy tới khi không còn job lấy được và không còn task (test, script)."""
        while True:
            await self.fill()
            if not self._tasks:
                return
            await asyncio.wait(set(self._tasks), return_when=asyncio.FIRST_COMPLETED)

    # ---------- lấy job ----------

    async def _limit(self) -> int:
        """BR-8.15: concurrency lớn nhất trong các job đang chờ / chạy, kẹp trong 1..8."""
        async with self._sessions() as s:
            raw = await s.scalar(
                select(func.max(Job.run_config["deepseek"]["concurrency"].as_integer()))
                .where(Job.engine == ENGINE, Job.status.in_(("queued", "running")))
            )
        return max(1, min(MAX_CONCURRENCY, int(raw or DEFAULT_CONCURRENCY)))

    async def fill(self) -> int:
        if self._stop.is_set():
            return 0
        limit = await self._limit()
        started = 0
        while len(self._tasks) < limit:
            job_id = await self.claim_next()
            if job_id is None:
                break
            task = asyncio.create_task(self.run_job(job_id))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            started += 1
        return started

    async def claim_next(self) -> uuid.UUID | None:
        async with self._sessions() as s:
            if await s.scalar(select(WorkerState.paused).where(WorkerState.engine == ENGINE)):
                return None
            job = (await s.scalars(
                select(Job)
                .where(Job.engine == ENGINE, Job.status == "queued", Job.kind.in_(POOL_KINDS))
                .order_by(Job.position, Job.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)  # BR-8.16
            )).first()
            if job is None:
                return None
            job.status = "running"
            job.attempts += 1
            if job.started_at is None:
                job.started_at = utcnow()
            chapter = await s.get(Chapter, job.chapter_id) if job.chapter_id else None
            if chapter is not None and job.kind != "review":
                chapter.status = "translating"
                await events.emit_chapter(s, chapter)
            await events.emit_job(s, job)
            await events.emit_book_stats(s, job.book_id)
            await s.commit()
            return job.id

    # ---------- chạy job ----------

    async def _load_job(self, job_id: uuid.UUID) -> PoolJob | None:
        """None khi job / chương đã bị xoá. Lỗi khác (DB) được ném ra để run_job đưa job về hàng đợi."""
        async with self._sessions() as s:
            job = await s.get(Job, job_id)
            chapter = await s.get(Chapter, job.chapter_id) if job and job.chapter_id else None
            if job is None or chapter is None:
                return None
            cfg = job.run_config or {}
            model_id = (((cfg.get("review") or {}).get("model_id") or "deepseek-flash") if job.kind == "review"
                        else deepseek_model(cfg))
            return PoolJob(job.id, job.book_id, chapter.id, chapter.no, job.kind, model_id, cfg,
                           job.options or {}, job.prev_chapter_status)

    async def _requeue_unloaded(self, job_id: uuid.UUID) -> None:
        """Job đã được claim (running) nhưng không nạp được: trả về hàng đợi, không để kẹt ở running."""
        async with self._sessions() as s:
            job = (await s.scalars(select(Job).where(Job.id == job_id).with_for_update())).first()
            if job is None or job.status != "running":
                await s.rollback()
                return
            job.status = "queued"
            chapter = await s.get(Chapter, job.chapter_id) if job.chapter_id else None
            if chapter is not None and job.kind != "review" and chapter.status == "translating":
                chapter.status = "queued"
                await events.emit_chapter(s, chapter)
            await events.emit_job(s, job)
            await s.commit()

    async def _job_kind(self, job_id: uuid.UUID) -> str | None:
        async with self._sessions() as s:
            return await s.scalar(select(Job.kind).where(Job.id == job_id))

    async def run_job(self, job_id: uuid.UUID) -> None:
        started = time.perf_counter()
        if await self._job_kind(job_id) == "ai_extract":  # G9: job trích không gắn với một chương
            await self._extract.run(job_id)
            return
        try:
            p = await self._load_job(job_id)
        except Exception:
            log.exception("Không nạp được job DeepSeek %s, đưa về hàng đợi", job_id)
            try:
                await self._requeue_unloaded(job_id)
            except Exception:
                log.exception("Không đưa được job DeepSeek %s về hàng đợi", job_id)
            return
        if p is None:
            return
        try:
            if p.kind == "review":
                await self._review(p, started)
            else:
                await self._translate(p, started)
            self._fail_streak = 0
        except JobInterrupted as e:
            await self._safely(self._interrupted(p, e.reason), p)
        except DeepSeekAuthError as e:
            await self._safely(self._auth_failed(p, e), p)
        except Exception as e:
            await self._safely(self._fail(p, e, started), p)
            self._fail_streak += 1
            if self._fail_streak >= FAIL_LIMIT:
                self._fail_streak = 0
                await self._safely(self._trip(
                    "failures", f"{FAIL_LIMIT} chương DeepSeek liên tiếp bị lỗi, đã tạm dừng pool DeepSeek để tránh tốn tiền", p), p)

    async def _safely(self, coro, p: PoolJob) -> None:
        try:
            await coro
        except Exception:
            log.exception("Không dọn được job DeepSeek %s", p.job_id)

    async def _check_interrupt(self, p: PoolJob) -> None:
        async with self._sessions() as s:
            status = await s.scalar(select(Job.status).where(Job.id == p.job_id))
            paused = await s.scalar(select(WorkerState.paused).where(WorkerState.engine == ENGINE))
        if status in (None, "cancelled"):
            raise JobInterrupted("cancelled")
        if self._stop.is_set():
            raise JobInterrupted("stopped")
        if paused:
            raise JobInterrupted("paused")

    async def _warn(self, p: PoolJob, message: str, *, usage: Usage | None = None) -> None:
        """Log warn của job (không ghi token vào cột để không đếm trùng chi phí; token nằm trong detail)."""
        async with self._sessions() as s:
            await logs.write_log(
                s, level="warn", source=_LOG_SOURCE.get(p.kind, "translate"), book_id=p.book_id,
                chapter_id=p.chapter_id, chapter_no=p.chapter_no, job_id=p.job_id, provider=ENGINE, model=p.model_id,
                message=message,
                detail={"usage": {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens}}
                if usage else {},
            )
            await s.commit()

    def _retry_logger(self, p: PoolJob):
        async def hook(attempt: int, reason: str) -> None:
            if reason.startswith("HTTP 429"):
                await self._warn(p, f"Chương {p.chapter_no} · {reason}")
            else:
                await self._warn(p, f"Chương {p.chapter_no} · {reason}, thử lại (lượt {attempt + 1}/{MAX_ATTEMPTS})")
        return hook

    # ---------- dịch ----------

    async def _translate(self, p: PoolJob, started: float) -> None:
        client = self._client_factory()
        async with self._sessions() as s:
            book = await s.get(Book, p.book_id)
            chapter = await s.get(Chapter, p.chapter_id)
            if book is None or chapter is None:
                raise JobInterrupted("cancelled")
            model = await ai_cost.get_model(s, p.model_id)
            coeff = await ai_cost.load_coefficients(s, p.book_id, p.model_id)
            tc = await load_translate_context(s, book, chapter, p.run_config)
        done = await self._prepare_staging(p, tc.source_hash)
        ds = p.run_config.get("deepseek") or {}
        temperature = float(ds.get("temperature", 0.3))
        window = model.context_window if model else FALLBACK_CONTEXT_WINDOW
        max_out = model.max_output_tokens if model else FALLBACK_MAX_OUTPUT
        fixed = prompt_tokens_est(tc.system, prompts.user_prompt(glossary_lines=tc.glossary.lines, context=tc.context,
                                                                 notes=tc.notes, lines=[]), coeff)
        budget_tokens = part_token_budget(context_window=window, max_output=max_out, fixed_tokens=fixed, coeff=coeff)
        pending = [(i, t) for i, t in tc.lines if i not in done]
        parts = prompts.split_lines(pending, budget_tokens, coeff.han_per_token)
        stats = RunStats(parts=len(parts))
        budget = RateBudget()  # BR-8.12: dùng chung cho cả chương
        for part in parts:
            await self._check_interrupt(p)
            rows = await self._translate_part(p, client, tc, part, temperature=temperature, max_out=max_out,
                                              coeff=coeff, stats=stats, budget=budget)
            done |= {r.idx for r in rows}
            await self._save_part(p, rows, len(done), len(tc.lines))
        await self._finish_translate(p, tc, stats, started)

    def _body(self, p: PoolJob, system: str, user: str, part: list[tuple[int, str]], *, temperature: float,
              max_out: int, coeff: Coefficients, stats: RunStats) -> tuple[dict, RequestEst]:
        lines_text = "\n".join(t for _, t in part)
        out_est = output_tokens_est(lines_text, coeff)
        out_cap = output_tokens_est(lines_text, Coefficients(coeff.han_per_token, max(coeff.out_ratio, SAFE_OUT_RATIO)))
        stats.tokens_in_est += prompt_tokens_est(system, user, coeff)
        stats.tokens_out_est += out_est
        stats.text_tokens += text_tokens(lines_text)
        overhead = prompt_tokens_est(system, user, Coefficients()) - text_tokens(lines_text)
        stats.overhead_tokens += overhead
        body = build_request(p.model_id, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                             temperature=temperature, max_tokens=min(max_out, max(MIN_MAX_TOKENS, math.ceil(out_cap * OUT_HEADROOM))))
        return body, RequestEst(prompt_tokens_est(system, user, coeff), out_est, text_tokens(lines_text), overhead)

    def _usage_hook(self, p: PoolJob, source: str, body: dict, est: RequestEst):
        """Mỗi lượt gọi đã nhận usage (kể cả lượt bị từ chối) ghi ngay một dòng log `partial`: đây là nguồn duy nhất của
        chi phí tháng và hiệu chỉnh, nên chi phí không mất khi job lỗi / tạm dừng / huỷ. Dòng tổng của chương chỉ để hiển thị."""
        async def hook(c: Completion) -> None:
            u = c.usage
            params = {"partial": True, "attempt": c.attempts, "max_tokens": body.get("max_tokens"),
                      "finish_reason": c.finish_reason}
            if source == "translate" and c.finish_reason != "length":  # lượt bị cắt cụt làm lệch hệ số hiệu chỉnh
                params["estimate"] = {"text_tokens": est.text_tokens, "overhead_tokens": est.overhead_tokens}
            async with self._sessions() as s:
                model = await ai_cost.get_model(s, p.model_id)
                await logs.write_log(
                    s, level="info", source=source, book_id=p.book_id, chapter_id=p.chapter_id, chapter_no=p.chapter_no,
                    job_id=p.job_id, provider=ENGINE, model=p.model_id,
                    message=f"Chương {p.chapter_no} · request DeepSeek {p.model_id}: {u.prompt_tokens} → {u.completion_tokens} token",
                    tokens_in=u.prompt_tokens, tokens_out=u.completion_tokens, tokens_in_cached=u.prompt_cache_hit_tokens,
                    tokens_in_est=est.tokens_in, tokens_out_est=est.tokens_out, cost_usd=ai_cost.cost_for(model, u),
                    latency_ms=c.latency_ms, params=params,
                    detail={"usage": {"prompt_cache_hit_tokens": u.prompt_cache_hit_tokens,
                                      "prompt_cache_miss_tokens": u.prompt_cache_miss_tokens,
                                      "reasoning_tokens": u.reasoning_tokens}})
                await s.execute(update(Job).where(Job.id == p.job_id).values(
                    tokens_in=Job.tokens_in + u.prompt_tokens, tokens_out=Job.tokens_out + u.completion_tokens))
                await s.commit()
        return hook

    def _account(self, stats: RunStats, body: dict, completion: Completion) -> None:
        stats.usage = stats.usage + completion.usage
        stats.request_count += 1
        stats.attempts += completion.attempts
        stats.rate_waits += completion.rate_waits
        stats.latency_ms += completion.latency_ms
        if stats.first_request is None:
            stats.first_request = body

    def _row(self, p: PoolJob, tc: TranslateContext, idx: int, src: str, out: str, flags: list[str]) -> JobSegment:
        hits = list(dict.fromkeys(m.term.id for m in tc.index.find(src))) if tc.index else []
        return JobSegment(job_id=p.job_id, idx=idx, dst=tc.indent[idx] + out.strip(), flags=flags, glossary_hits=hits)

    async def _translate_part(self, p: PoolJob, client: DeepSeekClient, tc: TranslateContext,
                              part: list[tuple[int, str]], *, temperature: float, max_out: int, coeff: Coefficients,
                              stats: RunStats, budget: RateBudget) -> list[JobSegment]:
        sent = [i for i, _ in part]
        src_of = dict(part)
        user = prompts.user_prompt(glossary_lines=tc.glossary.lines, context=tc.context, notes=tc.notes, lines=part)
        body, est = self._body(p, tc.system, user, part, temperature=temperature, max_out=max_out, coeff=coeff, stats=stats)
        holder: dict = {}

        def validate(c: Completion) -> None:
            guards.check_not_chinese(c.content)  # G1
            text, removed = guards.strip_preamble(c.content)  # G6
            got = guards.parse_marked(text)
            guards.check_markers(sent, got)  # G2: thiếu ≥ 5% thì thử lại cả request
            holder["got"], holder["preamble"] = got, removed

        completion = await client.chat(body, validate=validate, on_retry=self._retry_logger(p), budget=budget,
                                       on_usage=self._usage_hook(p, "translate", body, est))
        self._account(stats, body, completion)
        await self._check_ratio(p, "translate", completion, text_tokens("\n".join(t for _, t in part), coeff.han_per_token))  # G5
        got: dict[int, str] = holder["got"]
        stats.preamble += int(holder["preamble"])
        fallback: set[int] = set()
        missing = guards.missing_markers(sent, got)
        if missing:  # G2: thiếu < 5%
            stats.resent += len(missing)
            got.update(await self._resend(p, client, tc, [(i, src_of[i]) for i in missing], temperature=temperature,
                                          max_out=max_out, coeff=coeff, stats=stats, budget=budget))
            still = guards.missing_markers(missing, got)
            if still:
                got.update(await self._fallback_ct2(tc, still))
                fallback = set(still)
                stats.fallback += len(still)
            await self._warn(p, f"Chương {p.chapter_no} · thiếu {len(missing)} dòng trong output, đã gửi lại"
                                + (f"; {len(still)} dòng dịch bằng HachimiMT" if still else ""))
        rows = []
        skipped = set(tc.glossary.skipped_ids)
        for i, src in part:
            flags = ["fallback_ct2"] if i in fallback else []
            check = guards.check_glossary(src, got[i], tc.index, tc.variants)  # G4: alias, rồi âm Hán Việt (G9)
            if check.autofixed:
                flags.append("glossary_autofixed")
            if check.missed:
                flags.append("glossary_miss")
                stats.missed_terms.update(check.missed)
            stats.missed_terms.update(t for t in check.autofixed if t in skipped)  # L3 không gửi mà model dịch khác
            if i not in fallback and guards.has_residual_han(check.dst):  # G3 (dòng HachimiMT không thuộc output DeepSeek)
                flags.append("residual_han")
            rows.append(self._row(p, tc, i, src, check.dst, flags))
        return rows

    async def _resend(self, p: PoolJob, client: DeepSeekClient, tc: TranslateContext, lines: list[tuple[int, str]], *,
                      temperature: float, max_out: int, coeff: Coefficients, stats: RunStats,
                      budget: RateBudget) -> dict[int, str]:
        """G2: gửi lại riêng các dòng thiếu một lần. Lỗi thì trả rỗng để các dòng đó đi đường HachimiMT."""
        user = prompts.user_prompt(glossary_lines=tc.glossary.lines, context=None, notes=tc.notes, lines=lines)
        body, est = self._body(p, tc.system, user, lines, temperature=temperature, max_out=max_out, coeff=coeff, stats=stats)
        holder: dict = {}

        def validate(c: Completion) -> None:
            guards.check_not_chinese(c.content)
            holder["got"] = guards.parse_marked(guards.strip_preamble(c.content)[0])

        try:
            completion = await client.chat(body, validate=validate, on_retry=self._retry_logger(p), budget=budget,
                                           on_usage=self._usage_hook(p, "translate", body, est))
        except DeepSeekAuthError:
            raise
        except (DeepSeekError, OutputRejected) as e:
            await self._warn(p, f"Chương {p.chapter_no} · gửi lại dòng thiếu không được: {e}")
            return {}
        self._account(stats, body, completion)
        await self._check_ratio(p, "translate", completion, text_tokens("\n".join(t for _, t in lines), coeff.han_per_token))
        wanted = {i for i, _ in lines}
        return {i: t for i, t in holder["got"].items() if i in wanted and t.strip()}

    async def _load_translator(self) -> Translator:
        async with self._translator_lock:
            if self._translator is None:
                if self._translator_factory is None:
                    raise RuntimeError("Không có HachimiMT để dịch các dòng DeepSeek bỏ sót")
                self._translator = await anyio.to_thread.run_sync(self._translator_factory)
        return self._translator

    async def _fallback_ct2(self, tc: TranslateContext, idxs: list[int]) -> dict[int, str]:
        """G2: dòng DeepSeek vẫn bỏ sót thì dịch bằng HachimiMT (có glossary placeholder như đường ct2)."""
        translator = await self._load_translator()
        plans = [tc.plan_of[i] for i in idxs]
        result = await anyio.to_thread.run_sync(
            lambda: translate_segments(plans, translator, beam=2, batch_size=8, glossary=tc.index))
        return {r.idx: r.dst.strip() for r in result.segments}

    async def _check_ratio(self, p: PoolJob, kind: str, completion: Completion, text_tokens_est: int) -> None:
        """G5: token ra so với token văn bản của request (lượt cuối). Ghi warn; 2 request bất thường liên tiếp trong
        pool thì ngắt mạch."""
        usage = completion.final_usage or completion.usage
        if not guards.ratio_abnormal(kind, text_tokens_est, usage.completion_tokens):
            self._abnormal_streak = 0
            return
        self._abnormal_streak += 1
        await self._warn(p, f"{'Trích glossary' if p.chapter_no is None else f'Chương {p.chapter_no}'} · token ra bất thường ({usage.completion_tokens} ra / "
                            f"{text_tokens_est} token văn bản)", usage=usage)
        if self._abnormal_streak >= ABNORMAL_LIMIT:
            self._abnormal_streak = 0
            await self._trip("token_anomaly", "Token ra bất thường ở 2 request liên tiếp, đã tạm dừng pool DeepSeek "
                                              "để tránh tốn tiền", p)

    async def _trip(self, reason: str, message: str, p: PoolJob) -> None:
        async with self._sessions() as s:
            await queue.pause_engine(s, ENGINE, reason=reason, park_queued=False)
            await logs.write_log(s, level="error", source="system", book_id=p.book_id, chapter_id=p.chapter_id,
                                 chapter_no=p.chapter_no, job_id=p.job_id, provider=ENGINE, model=p.model_id,
                                 message=message)
            await s.commit()

    async def _auth_failed(self, p: PoolJob, exc: DeepSeekAuthError) -> None:
        """BR-8.13: dừng cả pool, mọi job DeepSeek đang chờ thành `paused`. Job này cũng tạm dừng, không đánh lỗi chương."""
        reason = "auth" if exc.status is not None else "no_key"
        async with self._sessions() as s:
            await queue.pause_engine(s, ENGINE, reason=reason, park_queued=True)
            job = (await s.scalars(select(Job).where(Job.id == p.job_id).with_for_update())).first()
            if job is None or job.status == "cancelled":
                await s.commit()
                await self._interrupted(p, "cancelled")
                return
            if job.status == "running":
                job.status = "paused"
            chapter = await s.get(Chapter, p.chapter_id)
            if chapter is not None and p.kind != "review":
                chapter.status = "queued"
                await events.emit_chapter(s, chapter)
            await logs.write_log(
                s, level="error", source="system", book_id=p.book_id, chapter_id=p.chapter_id, chapter_no=p.chapter_no,
                job_id=p.job_id, provider=ENGINE, model=p.model_id,
                message=f"{exc}. Đã tạm dừng pool DeepSeek, các job DeepSeek đang chờ chuyển sang tạm dừng.",
                detail={"error": {"type": type(exc).__name__, "status": exc.status, "message": str(exc)}},
            )
            await events.emit_job(s, job)
            await events.emit_book_stats(s, p.book_id)
            await s.commit()

    async def _prepare_staging(self, p: PoolJob, source_hash: str) -> set[int]:
        async with self._sessions() as s:
            job = await s.get(Job, p.job_id)
            if job is None:
                raise JobInterrupted("cancelled")
            if job.source_hash and job.source_hash != source_hash:  # bản gốc đã bị thay: làm lại từ đầu
                await s.execute(delete(JobSegment).where(JobSegment.job_id == p.job_id))
            job.source_hash = source_hash
            have = await s.scalar(select(func.count()).select_from(JobSegment).where(JobSegment.job_id == p.job_id))
            if not have and p.prev_status == "error" and p.kind != "review":
                # thử lại chương lỗi: nhận phần đã dịch của job lỗi gần nhất (cùng bản gốc, cùng model) để không tốn tiền lại
                for prev in (await s.scalars(select(Job).where(
                        Job.chapter_id == p.chapter_id, Job.id != p.job_id, Job.status == "failed",
                        Job.kind.in_(("translate", "retranslate"))).order_by(Job.finished_at.desc()))).all():
                    if prev.source_hash == source_hash and deepseek_model(prev.run_config or {}) == p.model_id:
                        await s.execute(update(JobSegment).where(JobSegment.job_id == prev.id).values(job_id=p.job_id))
                        break
            done = set((await s.scalars(select(JobSegment.idx).where(JobSegment.job_id == p.job_id))).all())
            await s.commit()
        return done

    async def _save_part(self, p: PoolJob, rows: list[JobSegment], done: int, total: int) -> None:
        async with self._sessions() as s:
            s.add_all(rows)
            await s.execute(update(Job).where(Job.id == p.job_id)
                            .values(progress=progress_pct(done, total), updated_at=utcnow()))
            job = await s.scalar(select(Job).where(Job.id == p.job_id))
            if job is not None:
                await events.emit_job(s, job)
            await s.commit()

    async def _finish_translate(self, p: PoolJob, tc: TranslateContext, stats: RunStats, started: float) -> None:
        await self._check_interrupt(p)
        async with self._sessions() as s:
            job = (await s.scalars(select(Job).where(Job.id == p.job_id).with_for_update())).first()
            if job is None or job.status != "running":  # bị huỷ đúng lúc này: không ghi đè
                await s.rollback()
                raise JobInterrupted("cancelled")
            await self._write_translation(s, p, tc, stats, job, started)
            await s.commit()

    async def _write_translation(self, s, p: PoolJob, tc: TranslateContext, stats: RunStats, job: Job,
                                 started: float) -> None:
        staged = {r.idx: r for r in (await s.scalars(select(JobSegment).where(JobSegment.job_id == p.job_id))).all()}
        missing = [i for i, _ in tc.lines if i not in staged]
        if missing:
            raise RuntimeError(f"Thiếu kết quả của {len(missing)} dòng")
        keep: dict[int, str] = {}
        if p.options.get("keep_manual_edits"):
            existing = (await s.scalars(select(Segment).where(Segment.chapter_id == p.chapter_id, Segment.edited))).all()
            keep = {seg.idx: seg.dst for seg in existing
                    if seg.idx in tc.plan_of and tc.plan_of[seg.idx].src == seg.src}

        prev = await versions.previous_versions(s, p.chapter_id)  # BR-6.10: đọc trước khi xoá
        await s.execute(delete(Segment).where(Segment.chapter_id == p.chapter_id))
        await review_service.supersede_pending(s, p.chapter_id)
        rows = []
        for plan in tc.plans:
            if plan.is_meta:
                rows.append({"chapter_id": p.chapter_id, "idx": plan.idx, "src": plan.src, "is_meta": True,
                             "dst": plan.src, "dst_machine": plan.src, "dst_model_raw": plan.src, "honorific_edits": [],
                             "edited": False, "flags": [], "glossary_hits": [], "dst_mt": None, "dst_ai": None})
                continue
            r = staged[plan.idx]
            edited = plan.idx in keep
            rows.append({"chapter_id": p.chapter_id, "idx": plan.idx, "src": plan.src, "is_meta": False,
                         "dst": keep[plan.idx] if edited else r.dst, "dst_machine": r.dst, "dst_model_raw": r.dst,
                         "honorific_edits": [], "edited": edited, "flags": list(r.flags),
                         "glossary_hits": list(r.glossary_hits),
                         **carried_versions(AI_COLUMN, plan.src, r.dst, prev.get(plan.idx))})
        if rows:
            await s.execute(insert(Segment), rows)

        status = chapter_status_after(row["flags"] for row in rows)
        now = utcnow()
        chapter = await s.get(Chapter, p.chapter_id)
        chapter.status, chapter.model_id, chapter.translated_at, chapter.error = status, p.model_id, now, None
        if tc.title_idx is not None and not chapter.title_vi_edited:  # BR-8.8
            title = guards.clean_title(staged[tc.title_idx].dst)
            if title:
                chapter.title_vi = title
        s.add(ChapterRevision(chapter_id=p.chapter_id, kind="machine", model_id=p.model_id, run_config=job.run_config,
                              segments_changed=len(tc.lines), snapshot=snapshot_rows(rows), changed_idx=[]))
        if tc.note_ids:  # AC-8.10: ghi chú đã đưa vào prompt thì coi như đã xử lý
            await s.execute(update(ChapterNote).where(ChapterNote.id.in_(tc.note_ids)).values(resolved=True, updated_at=now))
        if stats.missed_terms:  # G4: tăng miss_count; từ 3 lần thì tự bật always_send (G9)
            ids = [uuid.UUID(t) for t in stats.missed_terms]
            reach = GlossaryTerm.miss_count + 1 >= ALWAYS_SEND_AFTER_MISSES
            flipped = (await s.scalars(select(GlossaryTerm.src_zh).where(
                GlossaryTerm.id.in_(ids), GlossaryTerm.always_send.is_(False), reach).order_by(GlossaryTerm.src_zh))).all()
            await s.execute(update(GlossaryTerm).where(GlossaryTerm.id.in_(ids))
                            .values(miss_count=GlossaryTerm.miss_count + 1, always_send=or_(GlossaryTerm.always_send, reach)))
            if flipped:
                await logs.write_log(
                    s, level="info", source="glossary", book_id=p.book_id, chapter_id=p.chapter_id,
                    chapter_no=p.chapter_no, job_id=p.job_id,
                    message=(f"Tự bật “Luôn gửi” cho {len(flipped)} term bị dịch sai từ {ALWAYS_SEND_AFTER_MISSES} lần: "
                             + ", ".join(flipped[:10])))
        job.status, job.progress, job.finished_at, job.error = "done", 100, now, None
        await s.execute(delete(JobSegment).where(JobSegment.job_id == p.job_id))
        # phần dịch dở giữ lại của các job lỗi trước đó của chương đã hết tác dụng
        await s.execute(delete(JobSegment).where(JobSegment.job_id.in_(
            select(Job.id).where(Job.chapter_id == p.chapter_id, Job.status == "failed"))))
        if ((job.run_config.get("deepseek") or {}).get("auto_extract_glossary", True)
                and chapter.ai_scanned_at is None):  # BR-8.17, BR-8.18; chương đã quét thì dịch lại không quét lại
            await queue.enqueue_extract(s, await s.get(Book, p.book_id), chapter_ids=[p.chapter_id], model=p.model_id,
                                        categories=list(DEFAULT_CATEGORIES), auto=True)
        await self._log_translation(s, p, tc, stats, rows, status, started)
        await events.emit_job(s, job)
        await events.emit_chapter(s, chapter)
        await events.emit_book_stats(s, p.book_id)

    async def _log_translation(self, s, p: PoolJob, tc: TranslateContext, stats: RunStats, rows: list[dict],
                               status: str, started: float) -> None:
        model = await ai_cost.get_model(s, p.model_id)
        flag_counts: dict[str, int] = {}
        for row in rows:
            for f in row["flags"]:
                flag_counts[f] = flag_counts.get(f, 0) + 1
        flagged = sum(1 for row in rows if row["flags"])
        u = stats.usage
        totals = await ai_cost.job_usage_totals(s, p.job_id)  # cả các lượt trước khi tạm dừng
        message = f"Chương {p.chapter_no} · DeepSeek {p.model_id} · {len(tc.lines)} câu"
        if stats.parts > 1:
            message += f" · {stats.parts} phần"
        if flagged:
            message += f" · {flagged} câu có cờ" + (", cần soát" if status == "needs_review" else "")
        await logs.write_log(
            s, level="warn" if status == "needs_review" else "info", source="translate", book_id=p.book_id,
            chapter_id=p.chapter_id, chapter_no=p.chapter_no, job_id=p.job_id, provider=ENGINE, model=p.model_id,
            message=message, tokens_in=totals["tokens_in"], tokens_out=totals["tokens_out"],
            tokens_in_cached=totals["tokens_in_cached"], tokens_in_est=totals["tokens_in_est"],
            tokens_out_est=totals["tokens_out_est"], cost_usd=totals["cost_usd"],  # chỉ hiển thị; chi phí tính từ dòng `partial`
            latency_ms=int((time.perf_counter() - started) * 1000),
            params={"temperature": (stats.first_request or {}).get("temperature"),
                    "max_tokens": (stats.first_request or {}).get("max_tokens"), "parts": stats.parts,
                    "attempts": stats.attempts, "summary": True},
            detail={
                "usage": {"prompt_cache_hit_tokens": u.prompt_cache_hit_tokens,
                          "prompt_cache_miss_tokens": u.prompt_cache_miss_tokens, "reasoning_tokens": u.reasoning_tokens},
                "tokens_per_s": round(u.completion_tokens / (stats.latency_ms / 1000), 1) if stats.latency_ms else None,
                "glossary": tc.glossary.stats(),  # BR-8.3c
                "segments": {"total": len(tc.lines), "flagged": flagged, "flags": flag_counts, "resent": stats.resent,
                             "fallback_ct2": stats.fallback, "preamble_removed": stats.preamble},
                "requests": stats.request_count, "rate_waits": stats.rate_waits,
                "request": stats.first_request,  # write_log cắt mỗi chuỗi còn 2.000 ký tự (BR-5.5)
            },
        )

    # ---------- soát ----------

    async def _review(self, p: PoolJob, started: float) -> None:
        client = self._client_factory()
        async with self._sessions() as s:
            book = await s.get(Book, p.book_id)
            chapter = await s.get(Chapter, p.chapter_id)
            if book is None or chapter is None:
                raise JobInterrupted("cancelled")
            model = await ai_cost.get_model(s, p.model_id)
            coeff = await ai_cost.load_coefficients(s, p.book_id, p.model_id)
            if not self._still_reviewable(chapter):
                await self._skip_review(s, p, chapter)
                return
            rc = await load_review_context(s, book, chapter, p.run_config)
        stats = RunStats()
        fixes: list[dict] = []
        if rc.items:
            await self._check_interrupt(p)
            user = prompts.review_user_prompt(rc.glossary.lines, rc.items)
            stats.tokens_in_est = prompt_tokens_est(prompts.REVIEW_SYSTEM, user, coeff)
            stats.tokens_out_est = output_tokens_est("\n".join(it.src for it in rc.items), coeff, share=REVIEW_OUT_SHARE)
            max_out = model.max_output_tokens if model else FALLBACK_MAX_OUTPUT
            body = build_request(
                p.model_id, [{"role": "system", "content": prompts.REVIEW_SYSTEM}, {"role": "user", "content": user}],
                temperature=float((p.run_config.get("deepseek") or {}).get("temperature", 0.3)),
                max_tokens=review_max_tokens(len(rc.items), max_out), json_mode=True)
            holder: dict = {}

            def validate(c: Completion) -> None:
                holder["fixes"] = guards.parse_review_json(c.content)

            completion = await client.chat(body, validate=validate, on_retry=self._retry_logger(p), budget=RateBudget(),
                                           on_usage=self._usage_hook(p, "review", body,
                                                                     RequestEst(stats.tokens_in_est, stats.tokens_out_est)))
            self._account(stats, body, completion)
            review_text = "\n".join(f"{it.src}\n{it.dst}" for it in rc.items)
            await self._check_ratio(p, "review", completion, text_tokens(review_text, coeff.han_per_token))  # G5: soát > 1,5
            fixes = holder["fixes"]
        await self._check_interrupt(p)
        async with self._sessions() as s:
            job = (await s.scalars(select(Job).where(Job.id == p.job_id).with_for_update())).first()
            if job is None or job.status != "running":
                await s.rollback()
                raise JobInterrupted("cancelled")
            chapter = await s.get(Chapter, p.chapter_id, with_for_update=True)
            if not self._still_reviewable(chapter):  # chương đổi giữa lúc DeepSeek soát: bỏ kết quả
                await self._skip_review(s, p, chapter)
                return
            result = await review_service.store_fixes(
                s, chapter, fixes, model_id=p.model_id, auto_apply=(p.run_config.get("review") or {}).get("auto_apply", "none"))
            u = stats.usage
            job.status, job.progress, job.finished_at, job.error = "done", 100, utcnow(), None
            totals = await ai_cost.job_usage_totals(s, p.job_id)
            message = f"Chương {p.chapter_no} · soát bằng {p.model_id}: {result['stored']} đề xuất mới"
            if result["auto_applied"]:
                message += f", tự áp {result['auto_applied']}"
            if result["dropped_mismatch"]:
                message += f", bỏ {result['dropped_mismatch']} vì bản dịch đã đổi"
            await logs.write_log(
                s, level="info", source="review", book_id=p.book_id, chapter_id=p.chapter_id, chapter_no=p.chapter_no,
                job_id=p.job_id, provider=ENGINE, model=p.model_id, message=message,
                tokens_in=totals["tokens_in"] if totals["requests"] else None,
                tokens_out=totals["tokens_out"] if totals["requests"] else None,
                tokens_in_cached=totals["tokens_in_cached"] if totals["requests"] else None,
                tokens_in_est=stats.tokens_in_est or None, tokens_out_est=stats.tokens_out_est or None,
                cost_usd=totals["cost_usd"] if totals["requests"] else None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                params={"max_tokens": (stats.first_request or {}).get("max_tokens"), "attempts": stats.attempts,
                        "summary": True},
                detail={"review": result, "glossary": rc.glossary.stats(),
                        "usage": {"prompt_cache_hit_tokens": u.prompt_cache_hit_tokens,
                                  "reasoning_tokens": u.reasoning_tokens},
                        "request": stats.first_request},
            )
            await events.emit_job(s, job)
            await s.commit()

    @staticmethod
    def _still_reviewable(chapter: Chapter) -> bool:
        return review_service.reviewable(chapter) and chapter.status not in review_service.BUSY

    async def _skip_review(self, s, p: PoolJob, chapter: Chapter) -> None:
        """Chương không còn soát được (đang dịch lại, đổi model...): huỷ job, không gọi / không lưu kết quả."""
        job = (await s.scalars(select(Job).where(Job.id == p.job_id).with_for_update())).first()
        if job is not None and job.status == "running":
            job.status, job.finished_at = "cancelled", utcnow()
            job.error = "Chương không còn soát được (đang trong hàng đợi, đang dịch hoặc đã đổi model)"
            await events.emit_job(s, job)
        await logs.write_log(s, level="warn", source="review", book_id=p.book_id, chapter_id=p.chapter_id,
                             chapter_no=p.chapter_no, job_id=p.job_id, provider=ENGINE, model=p.model_id,
                             message=f"Chương {p.chapter_no} · bỏ qua soát: chương không còn soát được")
        await s.commit()

    # ---------- dừng giữa chừng / lỗi ----------

    async def _interrupted(self, p: PoolJob, reason: str) -> None:
        async with self._sessions() as s:
            job = (await s.scalars(select(Job).where(Job.id == p.job_id).with_for_update())).first()
            chapter = await s.get(Chapter, p.chapter_id)
            if job is None or job.status == "cancelled":  # huỷ thắng tạm dừng / dừng pool
                reason = "cancelled"
            touch_chapter = chapter is not None and p.kind != "review"  # job soát không đổi trạng thái chương
            if reason == "cancelled":
                await s.execute(delete(JobSegment).where(JobSegment.job_id == p.job_id))
                if touch_chapter and not await queue.has_other_active_job(s, p.chapter_id, p.job_id):
                    chapter.status = p.prev_status or "todo"
                message = f"Đã huỷ job DeepSeek chương {p.chapter_no}"
            else:
                if job is not None and job.status == "running":
                    job.status = "paused" if reason == "paused" else "queued"
                if touch_chapter:
                    chapter.status = "queued"
                message = (f"Tạm dừng job DeepSeek chương {p.chapter_no}" if reason == "paused"
                           else f"Dừng pool DeepSeek, chương {p.chapter_no} quay lại hàng đợi")
            await logs.write_log(s, level="info", source="system", book_id=p.book_id, chapter_id=p.chapter_id,
                                 chapter_no=p.chapter_no, job_id=p.job_id, provider=ENGINE, message=message)
            if job is not None:
                await events.emit_job(s, job)
            if chapter is not None:
                await events.emit_chapter(s, chapter)
            await events.emit_book_stats(s, p.book_id)
            await s.commit()

    async def _fail(self, p: PoolJob, exc: Exception, started: float) -> None:
        log.warning("Job DeepSeek %s lỗi: %s", p.job_id, exc)
        message = f"{type(exc).__name__}: {exc}"
        keep = keeps_staging(exc)
        async with self._sessions() as s:
            job = await s.get(Job, p.job_id)
            chapter = await s.get(Chapter, p.chapter_id)
            if job is not None and job.status == "cancelled":
                await s.rollback()
                await self._interrupted(p, "cancelled")
                return
            if not keep:  # lỗi có thể thử lại thì giữ phần đã dịch cho lần thử lại
                await s.execute(delete(JobSegment).where(JobSegment.job_id == p.job_id))
            if job is not None:
                job.status, job.error, job.finished_at = "failed", message[:1000], utcnow()
            if chapter is not None and p.kind != "review":
                chapter.status, chapter.error = "error", message[:1000]
            totals = await ai_cost.job_usage_totals(s, p.job_id)
            spent = totals["requests"] > 0
            await logs.write_log(
                s, level="error", source="review" if p.kind == "review" else "translate", book_id=p.book_id,
                chapter_id=p.chapter_id, chapter_no=p.chapter_no, job_id=p.job_id, provider=ENGINE, model=p.model_id,
                message=f"Chương {p.chapter_no} · DeepSeek lỗi: {message}",
                tokens_in=totals["tokens_in"] if spent else None, tokens_out=totals["tokens_out"] if spent else None,
                tokens_in_cached=totals["tokens_in_cached"] if spent else None,
                cost_usd=totals["cost_usd"] if spent else None,  # chỉ hiển thị; chi phí tính từ dòng `partial`
                latency_ms=int((time.perf_counter() - started) * 1000), params={"summary": True},
                detail={"error": {"type": type(exc).__name__, "status": getattr(exc, "status", None),
                                  "message": str(exc), "stack": "".join(traceback.format_exception(exc))}},
            )
            if job is not None:
                await events.emit_job(s, job)
            if chapter is not None:
                await events.emit_chapter(s, chapter)
            await events.emit_book_stats(s, p.book_id)
            await s.commit()
