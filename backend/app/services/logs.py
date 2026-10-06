import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import AppError
from app.models import Book, LogEntry, utcnow
from app.services import events

MAX_TEXT = 2000
RETENTION_DAYS = 30
SECRET_KEYS = {"authorization", "api_key", "apikey", "x-api-key", "deepseek_api_key"}
_AUTH_HEADER = re.compile(r"""(authorization["']?\s*[:=]\s*["']?)(?:(?:bearer|basic|token)\s+)?[^\s,;"'}]+""", re.I)
_BEARER = re.compile(r"(bearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.I)
_PARTITION = re.compile(r"^log_entries_(\d{4})_(\d{2})$")


def _scrub(s: str) -> str:
    key = get_settings().deepseek_api_key.strip()
    if key and key in s:
        s = s.replace(key, "***")
    s = _AUTH_HEADER.sub(r"\1***", s)
    s = _BEARER.sub(r"\1***", s)
    if len(s) > MAX_TEXT:
        s = s[:MAX_TEXT] + f"…(+{len(s) - MAX_TEXT} ký tự)"
    return s


def sanitize(value):
    """BR-5.5: không lưu key / Authorization, cắt chuỗi quá 2.000 ký tự."""
    if isinstance(value, dict):
        return {k: ("***" if str(k).lower() in SECRET_KEYS else sanitize(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        return _scrub(value)
    return value


def log_view(entry: LogEntry, *, with_detail: bool = True) -> dict:
    view = {
        "id": str(entry.id),
        "ts": entry.ts,
        "book_id": str(entry.book_id) if entry.book_id else None,
        "chapter_id": str(entry.chapter_id) if entry.chapter_id else None,
        "chapter_no": entry.chapter_no,
        "job_id": str(entry.job_id) if entry.job_id else None,
        "level": entry.level,
        "source": entry.source,
        "provider": entry.provider,
        "model": entry.model,
        "message": entry.message,
        "tokens_in": entry.tokens_in,
        "tokens_out": entry.tokens_out,
        "tokens_in_cached": entry.tokens_in_cached,
        "tokens_in_est": entry.tokens_in_est,
        "tokens_out_est": entry.tokens_out_est,
        "cost_usd": float(entry.cost_usd) if isinstance(entry.cost_usd, Decimal) else entry.cost_usd,
        "latency_ms": entry.latency_ms,
        "params": entry.params,
    }
    if with_detail:
        view["detail"] = entry.detail
    return view


async def write_log(
    session: AsyncSession,
    *,
    level: str,
    source: str,
    message: str,
    book_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
    chapter_no: int | None = None,
    job_id: uuid.UUID | None = None,
    provider: str | None = None,
    model: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    tokens_in_cached: int | None = None,
    tokens_in_est: int | None = None,
    tokens_out_est: int | None = None,
    cost_usd: float | Decimal | None = None,
    latency_ms: int | None = None,
    params: dict | None = None,
    detail: dict | None = None,
) -> LogEntry:
    entry = LogEntry(
        ts=utcnow(),
        book_id=book_id,
        chapter_id=chapter_id,
        chapter_no=chapter_no,
        job_id=job_id,
        level=level,
        source=source,
        provider=provider,
        model=model,
        message=_scrub(message),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_in_cached=tokens_in_cached,
        tokens_in_est=tokens_in_est,
        tokens_out_est=tokens_out_est,
        cost_usd=None if cost_usd is None else Decimal(str(round(float(cost_usd), 6))),
        latency_ms=latency_ms,
        params=sanitize(params or {}),
        detail=sanitize(detail or {}),
    )
    session.add(entry)
    await session.flush()
    await events.publish(session, "log.appended", book_id, log_view(entry, with_detail=False))
    return entry


async def _require_book(session: AsyncSession, book_id: uuid.UUID) -> Book:
    book = await session.get(Book, book_id)
    if book is None:
        raise AppError("BOOK_NOT_FOUND", "Không tìm thấy truyện", 404)
    return book


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtered(book_id, *, level=None, source=None, q=None, chapter_no=None):
    stmt = select(LogEntry).where(LogEntry.book_id == book_id)
    if level:
        stmt = stmt.where(LogEntry.level == level)
    if source:
        stmt = stmt.where(LogEntry.source == source)
    if chapter_no is not None:
        stmt = stmt.where(LogEntry.chapter_no == chapter_no)
    if q and q.strip():
        stmt = stmt.where(LogEntry.message.ilike(f"%{_escape_like(q.strip())}%"))
    return stmt


async def list_logs(
    session: AsyncSession,
    book_id: uuid.UUID,
    *,
    level: str | None = None,
    source: str | None = None,
    q: str | None = None,
    chapter_no: int | None = None,
    before: uuid.UUID | None = None,
    limit: int = 200,
) -> tuple[list[dict], str | None]:
    await _require_book(session, book_id)
    stmt = _filtered(book_id, level=level, source=source, q=q, chapter_no=chapter_no)
    if before is not None:
        stmt = stmt.where(LogEntry.id < before)  # id là uuid7, tăng theo thời gian
    rows = (await session.scalars(stmt.order_by(LogEntry.id.desc()).limit(limit))).all()
    items = [log_view(r) for r in rows]
    next_cursor = items[-1]["id"] if len(items) == limit else None
    return items, next_cursor


async def export_lines(session: AsyncSession, book_id: uuid.UUID, **filters) -> list[str]:
    await _require_book(session, book_id)
    rows = (await session.scalars(_filtered(book_id, **filters).order_by(LogEntry.id))).all()
    return [json.dumps(log_view(r), ensure_ascii=False, default=str) for r in rows]


async def get_log(session: AsyncSession, log_id: uuid.UUID) -> dict:
    entry = (await session.scalars(select(LogEntry).where(LogEntry.id == log_id))).first()
    if entry is None:
        raise AppError("LOG_NOT_FOUND", "Không tìm thấy dòng log", 404)
    return log_view(entry)


async def log_summary(session: AsyncSession, book_id: uuid.UUID) -> dict:
    await _require_book(session, book_id)
    total, errors = (
        await session.execute(
            select(func.count(), func.count().filter(LogEntry.level == "error")).where(LogEntry.book_id == book_id)
        )
    ).one()
    return {"total": total, "errors": errors}


async def clear_logs(session: AsyncSession, book_id: uuid.UUID) -> None:
    await _require_book(session, book_id)
    await session.execute(delete(LogEntry).where(LogEntry.book_id == book_id))
    stamp = datetime.now().astimezone().strftime("%H:%M %d/%m/%Y")
    await write_log(session, level="info", source="system", book_id=book_id, message=f"Log đã được xoá lúc {stamp}")


async def ensure_partitions(session: AsyncSession, months_ahead: int = 2) -> None:
    await session.execute(text("SELECT ensure_log_partitions(:m)"), {"m": months_ahead})


def _partition_end(name: str) -> date | None:
    m = _PARTITION.match(name)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    return date(year + (month == 12), month % 12 + 1, 1)


async def purge_old_logs(
    session: AsyncSession, now: datetime | None = None, keep_days: int = RETENTION_DAYS
) -> list[str]:
    """Xoá partition đã hết hạn hẳn, rồi xoá các dòng cũ hơn `keep_days` trong partition còn lại."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=keep_days)
    names = (
        await session.execute(
            text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = 'log_entries'::regclass"
            )
        )
    ).scalars().all()
    dropped = []
    for name in names:
        end = _partition_end(name)
        if end is not None and end <= cutoff.date():
            await session.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
            dropped.append(name)
    await session.execute(delete(LogEntry).where(LogEntry.ts < cutoff))
    return dropped
