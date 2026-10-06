import uuid

import pytest
from sqlalchemy import select

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.models import Chapter, GlossaryTerm, JobSegment, LogEntry, Segment
from app.services import queue
from app.worker import Worker
from helpers import enqueue, make_book

pytestmark = pytest.mark.db


async def add_term(book_id, src, dst, **kw):
    async with get_sessionmaker()() as s:
        t = GlossaryTerm(book_id=uuid.UUID(book_id), src_zh=src, dst_vi=dst, category="character", **kw)
        s.add(t)
        await s.commit()
        return str(t.id)


async def test_worker_applies_glossary_and_stores_hits(api):
    # AC-4.1
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    tid = await add_term(book_id, "第1章第1", "Câu Một")
    await enqueue(book_id)
    await Worker(FakeTranslator).run_once()
    async with get_sessionmaker()() as s:
        segs = {x.idx: x for x in (await s.scalars(select(Segment).where(Segment.chapter_id == chapters[0].id))).all()}
        line = (await s.scalars(select(LogEntry).where(LogEntry.source == "translate"))).one()
    assert segs[3].dst == "VI<Câu Một句话。>" and segs[3].glossary_hits == [tid]
    assert segs[4].glossary_hits == []
    assert line.detail["segments"]["glossary_hits"] == 1


async def test_disabled_terms_ignored(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    await add_term(book_id, "第1章第1", "Câu Một", enabled=False)
    await enqueue(book_id)
    fake = FakeTranslator()
    await Worker(lambda: fake).run_once()
    assert "第1章第1句话。" in fake.calls[0]


async def test_glossary_snapshot_taken_once_per_run(api):
    # Review Focus 2
    book_id, chapters = await make_book(api, n_chapters=1, lines=4)
    await add_term(book_id, "第1章", "Chương Một")
    await enqueue(book_id)

    async def change_term(step_no):
        if step_no == 1:
            async with get_sessionmaker()() as s:
                term = (await s.scalars(select(GlossaryTerm))).one()
                term.dst_vi = "ĐỔI GIỮA CHỪNG"
                await s.commit()

    await Worker(FakeTranslator, lines_per_step=2, on_step=change_term).run_once()
    async with get_sessionmaker()() as s:
        dsts = [x.dst for x in (await s.scalars(select(Segment).where(Segment.chapter_id == chapters[0].id))).all() if not x.is_meta]
    assert all("ĐỔI GIỮA CHỪNG" not in d for d in dsts)
    assert sum("Chương Một" in d for d in dsts) == 5


async def test_lost_placeholder_makes_chapter_need_review(api):
    # AC-4.3
    from app.core.placeholders import PSEUDO_NAMES
    from app.core.translator import BatchResult

    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    await add_term(book_id, "第1章第1", "Câu Một")
    await enqueue(book_id)

    class Dropper(FakeTranslator):
        def translate(self, texts, *, beam, batch_size):
            r = super().translate(texts, beam=beam, batch_size=batch_size)
            outs = []
            for o in r.outputs:
                for n in PSEUDO_NAMES:
                    o = o.replace(n, "")
                outs.append(o.replace("第1章第1", "x"))
            return BatchResult(outs, r.tokens_in, r.tokens_out, r.truncated)

    await Worker(Dropper).run_once()
    async with get_sessionmaker()() as s:
        ch = await s.get(Chapter, chapters[0].id)
        seg = (await s.scalars(select(Segment).where(Segment.chapter_id == ch.id, Segment.idx == 3))).one()
        line = (await s.scalars(select(LogEntry).where(LogEntry.source == "translate"))).one()
        assert (await s.scalars(select(JobSegment))).all() == []
    assert ch.status == "needs_review" and "placeholder_lost" in seg.flags
    assert line.level == "warn" and line.detail["segments"]["placeholder_lost"] == 1
