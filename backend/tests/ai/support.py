import uuid

from sqlalchemy import select

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.deepseek.pool import DeepSeekPool
from app.models import GlossaryTerm, LogEntry, Segment
from helpers import make_book


async def ds_book(api, *, n: int = 1, lines: int = 3, concurrency: int = 1, model: str = "deepseek-v4-pro",
                  genre_cfg: dict | None = None, auto_extract: bool = False):
    """Truyện có file nguồn thật, engine DeepSeek. Mặc định tắt tự trích glossary (BR-8.17) để đếm request cho dễ."""
    book_id, chapters = await make_book(api, n_chapters=n, lines=lines)
    cfg = {"engine": "deepseek", "model_id": model,
           "deepseek": {"concurrency": concurrency, "auto_extract_glossary": auto_extract}, **(genre_cfg or {})}
    r = await api.patch(f"/api/v1/books/{book_id}", json={"run_config": cfg})
    assert r.status_code == 200, r.text
    return book_id, chapters


def pool_for(fake, translator=None) -> DeepSeekPool:
    return DeepSeekPool(fake.client, translator_factory=translator or FakeTranslator)


async def get(model, id_):
    async with get_sessionmaker()() as s:
        return await s.get(model, id_)


async def segments(chapter_id) -> list[Segment]:
    async with get_sessionmaker()() as s:
        return list((await s.scalars(select(Segment).where(Segment.chapter_id == chapter_id).order_by(Segment.idx))).all())


async def log_rows(**where) -> list[LogEntry]:
    async with get_sessionmaker()() as s:
        stmt = select(LogEntry).order_by(LogEntry.id)
        for k, v in where.items():
            stmt = stmt.where(getattr(LogEntry, k) == v)
        return list((await s.scalars(stmt)).all())


async def add_term(book_id, src: str, dst: str, **kw) -> str:
    async with get_sessionmaker()() as s:
        t = GlossaryTerm(book_id=uuid.UUID(str(book_id)), src_zh=src, dst_vi=dst, category="character", **kw)
        s.add(t)
        await s.commit()
        return str(t.id)


async def summaries(**where) -> list[LogEntry]:
    """Dòng tổng của chương (không gồm các dòng `partial` của từng request)."""
    return [r for r in await log_rows(**where) if (r.params or {}).get("partial") is not True]
