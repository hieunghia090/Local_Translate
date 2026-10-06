"""AI trích glossary (spec 04 mục 6): chạy các lô, ghi log từng lô, gộp, đếm số lần xuất hiện, lưu đề xuất pending.
Dùng chung cho job `ai_extract` (pool DeepSeek) và "Chạy thử" ở Tạo truyện (Task 9)."""
import math
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import anyio
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.glossary import GlossaryIndex, Term
from app.core.run_config import deepseek_model
from app.db import get_sessionmaker
from app.deepseek import extract as core
from app.deepseek.catalog import FALLBACK_MAX_OUTPUT
from app.deepseek.client import Completion, DeepSeekAuthError, DeepSeekClient, Usage, build_request
from app.deepseek.estimate import Coefficients, cost_usd, prompt_tokens_est, text_tokens
from app.errors import AppError
from app.ids import uuid7
from app.models import ACTIVE_JOB_STATUSES, AiModel, Book, Chapter, GlossarySuggestion, GlossaryTerm, Job, WorkerState, utcnow
from app.services import ai_cost, imports, logs, queue
from app.services.books import get_book_or_404
from app.services.paths import chapter_source_path
from app.worker import JobInterrupted

MIN_MAX_TOKENS = 1024
INSERT_CHUNK = 1000


@dataclass
class ExtractOutcome:
    groups: list[list[core.Proposal]] = field(default_factory=list)
    drops: core.Drops = field(default_factory=core.Drops)
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    batches_total: int = 0
    batches_done: int = 0
    scanned: list = field(default_factory=list)  # id các chương đã quét xong (mọi lô chứa chương đó đã xong)
    salvaged: int = 0

    def proposals(self) -> list[core.Proposal]:
        return core.merge_proposals(self.groups)


class ExtractFailed(Exception):
    """Một lô lỗi sau khi hết lượt thử, hoặc job bị dừng. `outcome` giữ kết quả các lô đã xong (AC-4.9)."""

    def __init__(self, cause: BaseException, outcome: ExtractOutcome):
        super().__init__(str(cause))
        self.cause = cause
        self.outcome = outcome


def _scanned(batches: list[core.Batch], done: int, order: list) -> list:
    last: dict = {}
    for i, b in enumerate(batches):
        for cid in b.chapter_ids:
            last[cid] = i
    return [cid for cid in order if last.get(cid, -1) < done]


def batch_body(model_id: str, batch: core.Batch, categories, max_out: int) -> tuple[dict, int, int]:
    user = core.extract_user_prompt(batch.text, categories)
    out_est = math.ceil(text_tokens(batch.text) * core.OUT_SHARE)
    body = build_request(model_id, [{"role": "system", "content": core.EXTRACT_SYSTEM}, {"role": "user", "content": user}],
                         temperature=core.TEMPERATURE, max_tokens=min(max_out, max(MIN_MAX_TOKENS, out_est * 2)),
                         json_mode=True)
    return body, prompt_tokens_est(core.EXTRACT_SYSTEM, user, Coefficients()), out_est


async def _log(**kw) -> None:
    async with get_sessionmaker()() as s:
        await logs.write_log(s, source="glossary", provider="deepseek", **kw)
        await s.commit()


async def run_batches(client: DeepSeekClient, *, model_id: str, model: AiModel | None,
                      chapters: list[core.ChapterText], known: set[str], rejected: set[str], categories,
                      book_id: uuid.UUID | None, job_id: uuid.UUID | None = None,
                      check: Callable[[], Awaitable[None]] | None = None,
                      on_usage: Callable[[Completion, int], Awaitable[None]] | None = None,
                      on_progress: Callable[[int, int], Awaitable[None]] | None = None) -> ExtractOutcome:
    batches = core.make_batches(chapters)
    order = [c.chapter_id for c in chapters]
    out = ExtractOutcome(batches_total=len(batches), scanned=_scanned(batches, 0, order))
    max_out = model.max_output_tokens if model else FALLBACK_MAX_OUTPUT
    for i, batch in enumerate(batches, start=1):
        body, in_est, out_est = batch_body(model_id, batch, categories, max_out)
        holder: dict = {}

        def validate(c: Completion) -> None:
            holder["parsed"] = core.parse_terms(c.content)

        async def on_retry(attempt: int, reason: str, _i: int = i) -> None:
            suffix = "" if reason.startswith("HTTP 429") else f", thử lại (lượt {attempt + 1}/3)"
            await _log(level="warn", book_id=book_id, job_id=job_id, model=model_id,
                       message=f"Trích glossary lô {_i}/{len(batches)} · {reason}{suffix}")

        t0 = time.perf_counter()
        try:
            if check is not None:
                await check()
            completion = await client.chat(body, validate=validate, on_retry=on_retry)
        except (Exception, JobInterrupted) as e:
            raise ExtractFailed(e, out) from e
        raw, salvaged = holder["parsed"]
        kept, drops = core.filter_proposals(raw, batch.text, known=known, rejected=rejected, categories=categories)
        u = completion.usage
        cost = ai_cost.cost_for(model, u) or 0.0
        out.groups.append(kept)
        out.drops.add(drops)
        out.usage = out.usage + u
        out.cost_usd += cost
        out.batches_done = i
        out.salvaged += int(salvaged)
        out.scanned = _scanned(batches, i, order)
        message = f"Trích glossary lô {i}/{len(batches)} · chương {batch.first_no}–{batch.last_no} · {len(kept)}/{len(raw)} mục giữ lại"
        if salvaged:
            message += f" · output bị cắt, cứu được {len(raw)} mục"
        await _log(level="warn" if salvaged else "info", book_id=book_id, job_id=job_id, model=model_id, message=message,
                   tokens_in=u.prompt_tokens, tokens_out=u.completion_tokens, tokens_in_cached=u.prompt_cache_hit_tokens,
                   tokens_in_est=in_est, tokens_out_est=out_est, cost_usd=cost,
                   latency_ms=int((time.perf_counter() - t0) * 1000),
                   params={"temperature": core.TEMPERATURE, "max_tokens": body["max_tokens"], "batch": i,
                           "batches": len(batches), "chars": len(batch.text), "attempts": completion.attempts},
                   detail={"usage": {"prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens,
                                     "prompt_cache_hit_tokens": u.prompt_cache_hit_tokens,
                                     "prompt_cache_miss_tokens": u.prompt_cache_miss_tokens,
                                     "reasoning_tokens": u.reasoning_tokens},
                           "dropped": drops.view(), "proposed": len(raw), "kept": len(kept), "salvaged": salvaged,
                           "chapters": [batch.first_no, batch.last_no], "request": body})
        if on_usage is not None:
            await on_usage(completion, text_tokens(batch.text))
        if on_progress is not None:
            await on_progress(i, len(batches))
    return out


def _paths(book: Book, rows) -> list:
    return [chapter_source_path(book.slug, f or f"{no:04d}.txt") for no, f in rows]


async def load_chapters(session: AsyncSession, book: Book, chapter_ids: list[uuid.UUID]) -> list[core.ChapterText]:
    if not chapter_ids:
        return []
    rows = (await session.execute(select(Chapter.id, Chapter.no, Chapter.source_file)
                                  .where(Chapter.book_id == book.id, Chapter.id.in_(chapter_ids)).order_by(Chapter.no))).all()
    items = [(cid, no, chapter_source_path(book.slug, f or f"{no:04d}.txt")) for cid, no, f in rows]

    def read() -> list[core.ChapterText]:
        out = []
        for cid, no, path in items:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                text = ""
            out.append(core.ChapterText(cid, no, text))
        return out

    return await anyio.to_thread.run_sync(read)


async def known_sources(session: AsyncSession, book_id) -> tuple[set[str], set[str]]:
    """(src_zh của glossary, src_zh đề xuất đã chấp nhận hoặc từ chối)."""
    known = set((await session.scalars(select(GlossaryTerm.src_zh).where(GlossaryTerm.book_id == book_id))).all())
    decided = set((await session.scalars(select(GlossarySuggestion.src_zh).where(
        GlossarySuggestion.book_id == book_id, GlossarySuggestion.status != "pending"))).all())
    return known, decided


async def count_in_book(session: AsyncSession, book: Book, srcs: list[str]) -> dict[str, int]:
    """Mục 6 bước 6: số lần xuất hiện trong toàn bộ bản gốc."""
    if not srcs:
        return {}
    rows = (await session.execute(select(Chapter.no, Chapter.source_file).where(Chapter.book_id == book.id))).all()
    paths = _paths(book, rows)
    index = GlossaryIndex(Term(s, s, s) for s in srcs)

    def count() -> dict[str, int]:
        total: dict[str, int] = {}
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for k, v in index.count(text).items():
                total[k] = total.get(k, 0) + v
        return total

    return await anyio.to_thread.run_sync(count)


async def store_suggestions(session: AsyncSession, book_id, proposals: list[core.Proposal], counts: dict[str, int], *,
                            model_id: str) -> dict:
    """Lưu đề xuất pending. Mục pending cũ được cập nhật; mục đã chấp nhận / từ chối, hoặc src đã thành term (kể cả
    term vừa thêm trong lúc job chạy) thì bỏ qua."""
    known, decided = await known_sources(session, book_id)
    fresh = [p for p in proposals if p.src not in known and p.src not in decided]
    now = utcnow()
    rows = [{"id": uuid7(), "book_id": book_id, "src_zh": p.src, "dst_vi": p.dst, "category": p.category,
             "name_lang": p.name_lang, "notes": p.notes or None, "context": p.context or None, "confidence": p.confidence,
             "occurrence_count": counts.get(p.src, 0), "provider": "deepseek", "model": model_id, "status": "pending",
             "created_at": now, "updated_at": now} for p in fresh]
    for i in range(0, len(rows), INSERT_CHUNK):
        stmt = pg_insert(GlossarySuggestion).values(rows[i:i + INSERT_CHUNK])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_glossary_suggestions_book_src",
            set_={k: stmt.excluded[k] for k in ("dst_vi", "category", "name_lang", "notes", "context", "confidence",
                                                "occurrence_count", "model", "updated_at")},
            where=GlossarySuggestion.status == "pending",
        )
        await session.execute(stmt)
    return {"stored": len(fresh), "late_existing": len(proposals) - len(fresh)}


async def mark_scanned(session: AsyncSession, chapter_ids: list) -> None:
    ids = [c for c in chapter_ids if c is not None]
    if ids:
        await session.execute(update(Chapter).where(Chapter.id.in_(ids)).values(ai_scanned_at=utcnow()))


# ---------- API: bắt đầu job, ước tính ----------

async def resolve_scope(session: AsyncSession, book_id, scope) -> list[Chapter]:
    stmt = select(Chapter).where(Chapter.book_id == book_id)
    if scope.mode == "unscanned":
        stmt = stmt.where(Chapter.ai_scanned_at.is_(None))
    elif scope.mode == "range":
        stmt = stmt.where(Chapter.no >= scope.from_, Chapter.no <= scope.to)
    stmt = stmt.order_by(Chapter.no)
    if scope.mode == "first_n":
        stmt = stmt.limit(scope.n)
    return list((await session.scalars(stmt)).all())


async def start_extract(session: AsyncSession, book_id, body) -> tuple[Job, int]:
    book = await get_book_or_404(session, book_id, lock=True)
    active = (await session.scalars(select(Job).where(Job.book_id == book.id, Job.kind == "ai_extract",
                                                      Job.status.in_(ACTIVE_JOB_STATUSES)))).all()
    if any(not (j.options or {}).get("auto") for j in active):
        raise AppError("EXTRACT_BUSY", "Truyện đang có job trích glossary trong hàng đợi", 409)
    chapters = await resolve_scope(session, book.id, body.scope)
    if not chapters:
        raise AppError("NO_CHAPTERS_TO_SCAN", "Không có chương nào trong phạm vi đã chọn", 422)
    job = await queue.enqueue_extract(session, book, chapter_ids=[c.id for c in chapters],
                                      model=body.model or deepseek_model(book.run_config), categories=body.categories)
    await session.commit()
    return job, len(chapters)


async def estimate_extract(session: AsyncSession, book_id, body) -> dict:
    book = await get_book_or_404(session, book_id)
    model_id = body.model or deepseek_model(book.run_config)
    model = await ai_cost.get_model(session, model_id)
    if model is None:
        raise AppError("UNKNOWN_MODEL", f"Không có model {model_id}", 422)
    chapters = await resolve_scope(session, book.id, body.scope)
    batches = core.make_batches(await load_chapters(session, book, [c.id for c in chapters]))
    return _estimate_view(model_id, model, len(chapters), batches, body.categories)


def _estimate_view(model_id: str, model: AiModel, n_chapters: int, batches: list[core.Batch], categories) -> dict:
    coeff = Coefficients()
    tokens_in = sum(prompt_tokens_est(core.EXTRACT_SYSTEM, core.extract_user_prompt(b.text, categories), coeff)
                    for b in batches)
    tokens_out = sum(math.ceil(text_tokens(b.text) * core.OUT_SHARE) for b in batches)
    cached = text_tokens(core.EXTRACT_SYSTEM) * max(0, len(batches) - 1)
    cost = cost_usd(tokens_in=tokens_in, tokens_in_cached=cached, tokens_out=tokens_out,
                    price_in=float(model.price_in_per_mtok), price_cached=float(model.price_in_cached_per_mtok),
                    price_out=float(model.price_out_per_mtok))
    return {"model_id": model_id, "chapters": n_chapters, "batches": len(batches), "tokens_in": tokens_in,
            "tokens_in_cached": cached, "tokens_out": tokens_out, "cost_usd": cost,
            "prices_are_samples": model.prices_are_samples}


async def _import_texts(imp, n: int) -> list[core.ChapterText]:
    rows = imports.selected_chapters(imp)[:n]
    paths = [imports.chapter_text_path(imp.id, ch["key"]) for ch, _ in rows]
    return await anyio.to_thread.run_sync(
        lambda: [core.ChapterText(None, i, p.read_text(encoding="utf-8")) for i, p in enumerate(paths, start=1)])


async def estimate_import(session: AsyncSession, import_id: uuid.UUID, body) -> dict:
    """Ước tính chi phí lượt chạy thử / job ai_extract của khối AI khi tạo truyện (N chương đầu được giữ). Không gọi API."""
    imp = await imports.get_import(session, import_id)
    if imp.status != "ready":
        raise AppError("IMPORT_NOT_READY", "Đang phân tích file…", 409)
    model = await ai_cost.get_model(session, body.model)
    if model is None:
        raise AppError("UNKNOWN_MODEL", f"Không có model {body.model}", 422)
    texts = await _import_texts(imp, body.chapters)
    return _estimate_view(body.model, model, len(texts), core.make_batches(texts), body.categories)


PREVIEW_TIMEOUT = 120.0  # BR-4.9


def _preview_error(cause: BaseException) -> AppError:
    if isinstance(cause, DeepSeekAuthError):
        if cause.status is None:
            return AppError("DEEPSEEK_NO_KEY", "Chưa có DEEPSEEK_API_KEY trong file .env. Thêm key rồi khởi động lại app.", 409)
        return AppError("DEEPSEEK_AUTH", str(cause), 502)
    return AppError("AI_EXTRACT_FAILED", f"Chạy thử lỗi: {cause}", 502)


async def preview_import(session: AsyncSession, import_id: uuid.UUID, body, client: DeepSeekClient) -> dict:
    """Spec 02 mục 7: trích trên N chương đầu được giữ của ImportSession, đồng bộ, tối đa 120 giây. Không lưu đề xuất
    vào truyện: kết quả nằm ở import_sessions.ai_preview cho tới khi bấm Tạo."""
    imp = await imports.get_import(session, import_id)
    if imp.status != "ready":
        raise AppError("IMPORT_NOT_READY", "Đang phân tích file…", 409)
    state = await session.get(WorkerState, "deepseek")
    if state is not None and state.paused:  # engine tạm dừng để tránh tốn tiền: không chạy thử
        raise AppError("ENGINE_PAUSED", queue.pause_message(True, state.paused_reason) or "Engine DeepSeek đang tạm dừng", 409)
    if not imports.selected_chapters(imp)[: body.chapters]:
        raise AppError("NO_CHAPTERS_SELECTED", "Chưa chọn chương nào", 422)
    token = imp.analysis_token
    texts = await _import_texts(imp, body.chapters)
    model = await ai_cost.get_model(session, body.model)
    await session.commit()  # không giữ transaction trong lúc gọi mạng
    try:
        with anyio.fail_after(PREVIEW_TIMEOUT):
            outcome = await run_batches(client, model_id=body.model, model=model, chapters=texts, known=set(),
                                        rejected=set(), categories=body.categories, book_id=None)
    except TimeoutError as e:
        raise AppError("AI_PREVIEW_TIMEOUT",
                       f"Chạy thử quá {int(PREVIEW_TIMEOUT)} giây. Giảm số chương rồi thử lại.", 504) from e
    except ExtractFailed as e:
        raise _preview_error(e.cause) from e
    proposals = outcome.proposals()
    index = GlossaryIndex(Term(p.src, p.src, p.src) for p in proposals)
    counts: dict[str, int] = {}
    for t in texts:
        for k, v in index.count(t.text).items():
            counts[k] = counts.get(k, 0) + v
    items = [{"id": str(uuid7()), "src_zh": p.src, "dst_vi": p.dst, "category": p.category, "name_lang": p.name_lang,
              "notes": p.notes or None, "context": p.context or None, "confidence": p.confidence,
              "occurrence_count": counts.get(p.src, 0), "selected": p.confidence >= 80} for p in proposals]
    imp = await imports.get_import(session, import_id, lock=True)
    if imp.analysis_token != token:
        raise AppError("IMPORT_CHANGED", "File vừa được phân tích lại trong lúc chạy thử. Bấm Chạy thử lần nữa.", 409)
    imp.ai_preview = items
    await session.commit()
    return {"items": items, "model": body.model, "chapters": len(texts), "batches": outcome.batches_total,
            "dropped": outcome.drops.view(), "tokens_in": outcome.usage.prompt_tokens,
            "tokens_out": outcome.usage.completion_tokens, "cost_usd": round(outcome.cost_usd, 6)}
