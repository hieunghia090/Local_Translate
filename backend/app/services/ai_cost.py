"""Bảng giá model, chi phí và hệ số ước tính đã hiệu chỉnh (BR-8.24 → BR-8.27)."""
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.run_config import deep_merge, deepseek_model
from app.core.textio import SourceDecodeError, UnsupportedEncodingError
from app.deepseek import prompts
from app.deepseek.client import Usage
from app.deepseek.context import book_system_prompt, max_terms, read_plans, text_lines
from app.deepseek.estimate import (
    REVIEW_OUT_SHARE, WINDOW, Coefficients, Sample, calibrate, cost_usd, error_pct, output_tokens_est,
    part_token_budget, prompt_tokens_est, text_tokens,
)
from app.deepseek.glossary_select import select_terms
from app.errors import AppError
from app.models import AiModel, Chapter, LogEntry, Segment, utcnow
from app.services import glossary as glossary_service
from app.services.books import get_book_or_404
from app.services.review import reviewable

PRICE_FIELDS = ("price_in_per_mtok", "price_in_cached_per_mtok", "price_out_per_mtok")


async def get_model(session: AsyncSession, model_id: str) -> AiModel | None:
    return await session.get(AiModel, model_id)


def cost_for(model: AiModel | None, usage: Usage | None) -> float | None:
    if model is None or usage is None:
        return None
    return cost_usd(tokens_in=usage.prompt_tokens, tokens_in_cached=usage.prompt_cache_hit_tokens,
                    tokens_out=usage.completion_tokens, price_in=float(model.price_in_per_mtok),
                    price_cached=float(model.price_in_cached_per_mtok), price_out=float(model.price_out_per_mtok))


def _sample_filter(stmt, book_id: uuid.UUID, model_id: str):
    return stmt.where(LogEntry.book_id == book_id, LogEntry.model == model_id, LogEntry.provider == "deepseek",
                      LogEntry.source == "translate", LogEntry.tokens_in.is_not(None),
                      LogEntry.params.has_key("estimate"))


PARTIAL = LogEntry.params["partial"].as_boolean().is_(True)  # dòng log của từng request (nguồn duy nhất của chi phí)


async def job_usage_totals(session: AsyncSession, job_id: uuid.UUID) -> dict:
    """Tổng các dòng `partial` của một job (kể cả các lần chạy trước khi tạm dừng): để hiển thị ở dòng tổng của chương."""
    row = (await session.execute(
        select(func.count(), func.coalesce(func.sum(LogEntry.tokens_in), 0), func.coalesce(func.sum(LogEntry.tokens_out), 0),
               func.coalesce(func.sum(LogEntry.tokens_in_cached), 0), func.coalesce(func.sum(LogEntry.tokens_in_est), 0),
               func.coalesce(func.sum(LogEntry.tokens_out_est), 0), func.sum(LogEntry.cost_usd))
        .where(LogEntry.job_id == job_id, LogEntry.provider == "deepseek", PARTIAL)
    )).one()
    n, ti, to, tc, te_i, te_o, cost = row
    return {"requests": int(n), "tokens_in": int(ti), "tokens_out": int(to), "tokens_in_cached": int(tc),
            "tokens_in_est": int(te_i), "tokens_out_est": int(te_o), "cost_usd": None if cost is None else float(cost)}


async def load_samples(session: AsyncSession, book_id: uuid.UUID, model_id: str) -> list[Sample]:
    rows = (await session.execute(
        _sample_filter(select(LogEntry.tokens_in, LogEntry.tokens_out, LogEntry.params), book_id, model_id)
        .order_by(LogEntry.id.desc()).limit(WINDOW)
    )).all()
    out = []
    for tokens_in, tokens_out, params in rows:
        est = (params or {}).get("estimate") or {}
        out.append(Sample(int(tokens_in or 0), int(tokens_out or 0), int(est.get("text_tokens") or 0),
                          int(est.get("overhead_tokens") or 0)))
    return out


async def load_coefficients(session: AsyncSession, book_id: uuid.UUID, model_id: str) -> Coefficients:
    return calibrate(await load_samples(session, book_id, model_id))


def model_view(m: AiModel) -> dict:
    return {"id": m.id, "provider": m.provider, "label": m.label, "context_window": m.context_window,
            "max_output_tokens": m.max_output_tokens, "price_in_per_mtok": float(m.price_in_per_mtok),
            "price_in_cached_per_mtok": float(m.price_in_cached_per_mtok), "price_out_per_mtok": float(m.price_out_per_mtok),
            "prices_are_samples": m.prices_are_samples, "enabled": m.enabled}


async def list_models(session: AsyncSession) -> list[dict]:
    return [model_view(m) for m in await session.scalars(select(AiModel).order_by(AiModel.id))]


async def update_model(session: AsyncSession, model_id: str, changes: dict) -> dict:
    m = (await session.scalars(select(AiModel).where(AiModel.id == model_id).with_for_update())).first()
    if m is None:
        raise AppError("MODEL_NOT_FOUND", "Không tìm thấy model", 404)
    for key, value in changes.items():
        if value is None:
            continue
        if key in PRICE_FIELDS:
            setattr(m, key, Decimal(str(value)))
            m.prices_are_samples = False  # người dùng đã nhập giá thật
        else:
            setattr(m, key, value)
    m.updated_at = utcnow()
    await session.commit()
    return model_view(m)


async def estimate_batch(session: AsyncSession, book_id, *, action: str, chapter_ids: list[uuid.UUID] | None,
                         statuses: list[str] | None, model_id: str | None) -> dict:
    """BR-8.24, BR-8.26: dựng đúng prompt sẽ gửi (không có ngữ cảnh / ghi chú) rồi đếm token bằng hệ số đã hiệu chỉnh.
    Giả định phần system trúng cache ở mọi chương trừ chương đầu."""
    book = await get_book_or_404(session, book_id)
    stmt = select(Chapter).where(Chapter.book_id == book.id)
    if chapter_ids is not None:
        stmt = stmt.where(Chapter.id.in_(chapter_ids))
    elif statuses:
        stmt = stmt.where(Chapter.status.in_(statuses))
    else:
        raise AppError("ESTIMATE_TARGET_REQUIRED", "Cần chọn chương hoặc bộ lọc trạng thái", 422)
    chapters = list((await session.scalars(stmt.order_by(Chapter.no))).all())
    if action == "review":
        model_id = model_id or (book.run_config.get("review") or {}).get("model_id") or "deepseek-flash"
        eligible = [c for c in chapters if reviewable(c)]
    else:
        model_id = model_id or deepseek_model(book.run_config)
        eligible = [c for c in chapters if c.status not in ("queued", "translating")]
    model = await get_model(session, model_id)
    if model is None:
        raise AppError("UNKNOWN_MODEL", f"Không có model {model_id}", 422)
    coeff = await load_coefficients(session, book.id, model_id)
    index, terms = await glossary_service.load_prompt_terms(session, book.id)
    by_chapter: dict[uuid.UUID, list[Segment]] = defaultdict(list)
    if action == "review" and eligible:
        rows = await session.scalars(select(Segment).where(Segment.chapter_id.in_([c.id for c in eligible]),
                                                           Segment.is_meta.is_(False)).order_by(Segment.chapter_id, Segment.idx))
        for seg in rows:
            if seg.dst and seg.dst.strip():
                by_chapter[seg.chapter_id].append(seg)
    tokens_in = tokens_out = system_tokens = requests = 0
    for ch in eligible:
        cfg = deep_merge(book.run_config, ch.run_config_override or {})
        if action == "review":
            items = [prompts.ReviewItem(s.idx, s.src.strip(), s.dst.strip(), s.edited) for s in by_chapter[ch.id]]
            src_text = "\n".join(it.src for it in items)
            sel = select_terms(src_text, index, terms, max_terms(cfg))
            system, user = prompts.REVIEW_SYSTEM, prompts.review_user_prompt(sel.lines, items)
            tokens_out += output_tokens_est(src_text, coeff, share=REVIEW_OUT_SHARE)
            tokens_in += prompt_tokens_est(system, user, coeff)
            requests += 1
        else:
            try:
                lines = text_lines((await read_plans(book, ch))[0])
            except (OSError, SourceDecodeError, UnsupportedEncodingError):
                lines = []
            body = "\n".join(t for _, t in lines)
            sel = select_terms(body, index, terms, max_terms(cfg))
            system = book_system_prompt(book, cfg)
            user = prompts.user_prompt(glossary_lines=sel.lines, context=None, notes=[], lines=lines)
            tokens_in += prompt_tokens_est(system, user, coeff)
            tokens_out += output_tokens_est(body, coeff)
            # BR-8.7: chương dài chia nhiều phần; mỗi phần là một request mang system + glossary (không phải mỗi chương)
            fixed = prompt_tokens_est(system, prompts.user_prompt(glossary_lines=sel.lines, context=None, notes=[], lines=[]), coeff)
            budget = part_token_budget(context_window=model.context_window, max_output=model.max_output_tokens,
                                       fixed_tokens=fixed, coeff=coeff)
            n_parts = max(1, len(prompts.split_lines(lines, budget, coeff.han_per_token)))
            tokens_in += (n_parts - 1) * fixed
            requests += n_parts
            if not lines:  # không đọc được file nguồn: ước theo số chữ Hán
                approx = math.ceil(ch.char_count / coeff.han_per_token)
                tokens_in += approx
                tokens_out += math.ceil(approx * coeff.out_ratio)
        system_tokens = text_tokens(system, coeff.han_per_token)
    cached = system_tokens * max(0, requests - 1)
    cost = cost_usd(tokens_in=tokens_in, tokens_in_cached=cached, tokens_out=tokens_out,
                    price_in=float(model.price_in_per_mtok), price_cached=float(model.price_in_cached_per_mtok),
                    price_out=float(model.price_out_per_mtok))
    return {"action": action, "model_id": model_id, "chapters": len(eligible), "requests": requests, "skipped": len(chapters) - len(eligible),
            "tokens_in": tokens_in, "tokens_in_cached": cached, "tokens_out": tokens_out, "cost_usd": cost,
            "prices_are_samples": model.prices_are_samples, "coefficients": coeff.view()}


def month_range(month: str | None) -> tuple[datetime, datetime, str]:
    if month:
        year, mon = (int(x) for x in month.split("-"))
    else:
        now = datetime.now(timezone.utc)
        year, mon = now.year, now.month
    start = datetime(year, mon, 1, tzinfo=timezone.utc)
    end = datetime(year + (mon == 12), mon % 12 + 1, 1, tzinfo=timezone.utc)
    return start, end, f"{year:04d}-{mon:02d}"


async def accuracy_for(session: AsyncSession, book_id: uuid.UUID, model_id: str) -> dict:
    """BR-8.24c: sai số ước tính so với thực tế trên 50 request gần nhất, và hệ số đang dùng."""
    rows = (await session.execute(
        _sample_filter(select(LogEntry.tokens_in, LogEntry.tokens_in_est, LogEntry.tokens_out, LogEntry.tokens_out_est),
                       book_id, model_id).order_by(LogEntry.id.desc()).limit(WINDOW)
    )).all()
    return {"model": model_id, "requests": len(rows),
            "err_in_pct": error_pct([r[0] or 0 for r in rows], [r[1] for r in rows]),
            "err_out_pct": error_pct([r[2] or 0 for r in rows], [r[3] for r in rows]),
            "coefficients": (await load_coefficients(session, book_id, model_id)).view()}


async def usage_month(session: AsyncSession, book_id, month: str | None) -> dict:
    """BR-8.27: token và chi phí thật theo model và loại call trong tháng."""
    book = await get_book_or_404(session, book_id)
    start, end, label = month_range(month)
    rows = (await session.execute(
        select(LogEntry.model, LogEntry.source, func.count(), func.coalesce(func.sum(LogEntry.tokens_in), 0),
               func.coalesce(func.sum(LogEntry.tokens_in_cached), 0), func.coalesce(func.sum(LogEntry.tokens_out), 0),
               func.coalesce(func.sum(LogEntry.cost_usd), 0))
        .where(LogEntry.book_id == book.id, LogEntry.provider == "deepseek", LogEntry.tokens_in.is_not(None), PARTIAL,
               LogEntry.ts >= start, LogEntry.ts < end)
        .group_by(LogEntry.model, LogEntry.source).order_by(LogEntry.model, LogEntry.source)
    )).all()
    items = [{"model": m, "source": src, "requests": n, "tokens_in": int(ti), "tokens_in_cached": int(tc),
              "tokens_out": int(to), "cost_usd": round(float(c), 6)} for m, src, n, ti, tc, to, c in rows]
    total = {k: sum(i[k] for i in items) for k in ("requests", "tokens_in", "tokens_in_cached", "tokens_out")}
    total["cost_usd"] = round(sum(i["cost_usd"] for i in items), 6)
    models = (await session.scalars(select(AiModel.id).where(AiModel.enabled).order_by(AiModel.id))).all()
    return {"month": label, "items": items, "total": total,
            "accuracy": [await accuracy_for(session, book.id, m) for m in models]}


async def glossary_send_stats(session: AsyncSession, book_id, limit: int = 200) -> dict:
    """AC-8.13: số liệu L1–L4 và G4 từ log các chương dịch bằng DeepSeek gần nhất (BR-8.3c)."""
    book = await get_book_or_404(session, book_id)
    rows = (await session.scalars(
        select(LogEntry).where(LogEntry.book_id == book.id, LogEntry.provider == "deepseek", LogEntry.source == "translate",
                               LogEntry.detail.has_key("glossary"))
        .order_by(LogEntry.id.desc()).limit(limit))).all()
    if not rows:
        return {"chapters": 0, "avg_tokens_est": None, "avg_sent": None, "avg_skipped_predictable": None,
                "avg_truncated": None, "miss_pct": None, "autofixed_pct": None}
    g = [r.detail["glossary"] for r in rows]
    seg = [r.detail.get("segments") or {} for r in rows]
    total = sum(int(x.get("total") or 0) for x in seg)
    flags = lambda name: sum(int((x.get("flags") or {}).get(name) or 0) for x in seg)  # noqa: E731
    avg = lambda key: round(sum(float(x.get(key) or 0) for x in g) / len(g), 2)  # noqa: E731
    return {"chapters": len(rows), "avg_tokens_est": avg("tokens_est"), "avg_sent": avg("sent"),
            "avg_skipped_predictable": avg("skipped_predictable"), "avg_truncated": avg("truncated"),
            "miss_pct": round(100 * flags("glossary_miss") / total, 2) if total else None,
            "autofixed_pct": round(100 * flags("glossary_autofixed") / total, 2) if total else None}
