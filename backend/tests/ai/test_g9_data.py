import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import get_sessionmaker
from app.models import Book, Chapter, GlossarySuggestion, HanvietReading, HanvietUnknown, ImportSession, utcnow
from helpers import chapter_text, seed_book, upload

pytestmark = pytest.mark.db


def _suggestion(book_id, src: str, dst: str) -> GlossarySuggestion:
    now = utcnow()
    return GlossarySuggestion(book_id=book_id, src_zh=src, dst_vi=dst, category="character", confidence=95,
                              occurrence_count=3, provider="deepseek", model="deepseek-v4-pro", status="pending",
                              created_at=now, updated_at=now)


async def test_suggestion_unique_per_book_and_cascade(clean_db):
    book_id = await seed_book(statuses=["todo"])
    async with get_sessionmaker()() as s:
        ch = (await s.scalars(select(Chapter).where(Chapter.book_id == book_id))).one()
        assert ch.ai_scanned_at is None
        s.add(_suggestion(book_id, "赵楷", "Triệu Khải"))
        await s.commit()
    async with get_sessionmaker()() as s:
        s.add(_suggestion(book_id, "赵楷", "Triệu Giai"))
        with pytest.raises(IntegrityError):
            await s.commit()
    async with get_sessionmaker()() as s:
        sg = (await s.scalars(select(GlossarySuggestion))).one()
        assert (sg.status, sg.decided_at, sg.name_lang, sg.context) == ("pending", None, None, None)
        await s.delete(await s.get(Book, book_id))
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(GlossarySuggestion)) == 0


async def test_hanviet_tables(clean_db):
    async with get_sessionmaker()() as s:
        s.add_all([
            HanvietReading(char="藏", reading="tàng", source="ai", confidence=60, created_at=utcnow()),
            HanvietReading(char="藏", reading="tạng", source="confirmed", confidence=90, created_at=utcnow()),
            HanvietUnknown(char="𠀀", created_at=utcnow()),
        ])
        await s.commit()
    async with get_sessionmaker()() as s:
        rows = (await s.scalars(select(HanvietReading))).all()
        assert {(r.char, r.reading, r.source, r.confidence) for r in rows} == {("藏", "tàng", "ai", 60), ("藏", "tạng", "confirmed", 90)}
        s.add(HanvietReading(char="藏", reading="tàng", source="manual", confidence=100, created_at=utcnow()))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_import_session_preview_defaults_to_empty(api):
    view = await upload(api, [("0001.txt", chapter_text(1, 2))])
    async with get_sessionmaker()() as s:
        imp = await s.get(ImportSession, uuid.UUID(view["import_id"]))
    assert imp.ai_preview == []
