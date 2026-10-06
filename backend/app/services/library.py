import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.slug import fold
from app.errors import AppError
from app.models import CHAPTER_STATUSES, DONE_STATUSES, Book, Chapter

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def progress_pct(total: int, done: int) -> int:
    """BR-1.1, làm tròn nửa lên; chưa xong hết thì tối đa 99."""
    if not total:
        return 0
    pct = (done * 100 + total // 2) // total
    return min(pct, 99) if done < total else pct


def book_state(total: int, done: int) -> Literal["not_started", "in_progress", "completed"]:
    if done == 0:
        return "not_started"
    return "completed" if done == total else "in_progress"


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _folded(column):
    return func.f_unaccent(func.lower(func.coalesce(column, "")))


async def _status_counts(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    counts: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    if ids:
        rows = await session.execute(
            select(Chapter.book_id, Chapter.status, func.count())
            .where(Chapter.book_id.in_(ids))
            .group_by(Chapter.book_id, Chapter.status)
        )
        for book_id, status, n in rows:
            counts[book_id][status] = n
    return counts


def _item(b: Book, counts: dict[str, int]) -> dict:
    stats = {s: counts.get(s, 0) for s in CHAPTER_STATUSES}
    total = sum(stats.values())
    done = sum(stats[s] for s in DONE_STATUSES)
    return {
        "id": str(b.id),
        "slug": b.slug,
        "title_zh": b.title_zh,
        "title_vi": b.title_vi,
        "author": b.author,
        "genre": b.genre,
        "cover_url": None,
        "stats": {"total": total, **stats},
        "progress_pct": progress_pct(total, done),
        "state": book_state(total, done),
        "last_opened_at": b.last_opened_at,
        "created_at": b.created_at,
    }


def _name_key(item: dict) -> str:
    return fold(item["title_vi"] or item["title_zh"])


_SORTS = {
    "recent": lambda i: (i["last_opened_at"] is None, -(i["last_opened_at"] or _EPOCH).timestamp(),
                         -i["created_at"].timestamp()),
    "name": _name_key,
    "progress": lambda i: (-i["progress_pct"], _name_key(i)),
}


async def list_books(session: AsyncSession, *, q: str | None = None, filter: str = "all", sort: str = "recent") -> list[dict]:
    stmt = select(Book)
    if q and q.strip():
        raw = _escape_like(q.strip())
        pattern = func.concat("%", func.f_unaccent(func.lower(raw)), "%")
        stmt = stmt.where(
            or_(
                _folded(Book.title_vi).ilike(pattern),
                Book.title_zh.ilike(f"%{raw}%"),
                _folded(Book.author).ilike(pattern),
            )
        )
    books = (await session.scalars(stmt)).all()
    counts = await _status_counts(session, [b.id for b in books])
    items = [_item(b, counts.get(b.id, {})) for b in books]
    if filter == "completed":
        items = [i for i in items if i["state"] == "completed"]
    elif filter == "in_progress":
        items = [i for i in items if i["state"] != "completed"]
    items.sort(key=_SORTS[sort])
    return items


async def library_stats(session: AsyncSession) -> dict:
    books = await session.scalar(select(func.count()).select_from(Book))
    total, done = (
        await session.execute(
            select(func.count(), func.count().filter(Chapter.status.in_(DONE_STATUSES))).select_from(Chapter)
        )
    ).one()
    return {"books": books, "chapters_total": total, "chapters_done": done, "chapters_left": total - done}


async def open_book(session: AsyncSession, book_id: uuid.UUID) -> None:
    result = await session.execute(update(Book).where(Book.id == book_id).values(last_opened_at=func.now()))
    if result.rowcount == 0:
        raise AppError("BOOK_NOT_FOUND", "Không tìm thấy truyện", 404)
    await session.commit()


async def book_stats(session: AsyncSession, book_id: uuid.UUID) -> dict | None:
    book = await session.get(Book, book_id)
    if book is None:
        return None
    item = _item(book, (await _status_counts(session, [book_id])).get(book_id, {}))
    return {"stats": item["stats"], "progress_pct": item["progress_pct"], "state": item["state"]}
