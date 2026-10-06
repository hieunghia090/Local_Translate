import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import AiModel, Chapter, ChapterNote, ReviewFix, WorkerState, utcnow
from app.services import logs
from helpers import make_book, seed_book

pytestmark = pytest.mark.db


async def test_deepseek_tables_and_seed(clean_db):
    book_id = await seed_book(statuses=["translated"])
    async with get_sessionmaker()() as s:
        ch = (await s.scalars(select(Chapter).where(Chapter.book_id == book_id))).one()
        assert ch.title_vi_edited is False
        s.add(ReviewFix(chapter_id=ch.id, segment_idx=3, type="name_mismatch", before="a", after="b", confidence=95,
                        status="pending", model_id="deepseek-flash", created_at=utcnow()))
        s.add(ChapterNote(chapter_id=ch.id, type="correction", content="Tên 高俅 là Cao Cầu", resolved=False, created_at=utcnow()))
        await s.commit()
    async with get_sessionmaker()() as s:
        fix = (await s.scalars(select(ReviewFix))).one()
        note = (await s.scalars(select(ChapterNote))).one()
        models = {m.id: m for m in (await s.scalars(select(AiModel))).all()}
        ws = await s.get(WorkerState, "deepseek")
    assert (fix.status, fix.decided_at, fix.confidence) == ("pending", None, 95)
    assert (note.type, note.resolved) == ("correction", False)
    assert set(models) == {"deepseek-v4-pro", "deepseek-flash"}
    assert all(m.prices_are_samples and m.enabled and m.context_window > 0 and m.max_output_tokens > 0 for m in models.values())
    assert ws.paused_reason is None


async def test_write_log_stores_estimates_and_cost(clean_db):
    book_id = await seed_book()
    async with get_sessionmaker()() as s:
        e = await logs.write_log(s, level="info", source="translate", book_id=book_id, message="x", provider="deepseek",
                                 tokens_in=100, tokens_out=50, tokens_in_cached=40, tokens_in_est=90, tokens_out_est=60,
                                 cost_usd=0.0012)
        await s.commit()
        assert (e.tokens_in_cached, e.tokens_in_est, e.tokens_out_est, float(e.cost_usd)) == (40, 90, 60, 0.0012)
        assert logs.log_view(e)["cost_usd"] == 0.0012


async def test_patch_title_marks_title_edited(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    r = await api.patch(f"/api/v1/chapters/{chapters[0].id}", json={"title_vi": "Tên tôi đặt"})
    assert r.status_code == 200, r.text
    async with get_sessionmaker()() as s:
        assert (await s.get(Chapter, chapters[0].id)).title_vi_edited is True
    await api.patch(f"/api/v1/chapters/{chapters[0].id}", json={"title_vi": ""})
    async with get_sessionmaker()() as s:
        assert (await s.get(Chapter, chapters[0].id)).title_vi_edited is False
