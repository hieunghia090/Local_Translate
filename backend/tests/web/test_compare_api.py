import uuid

import pytest
from sqlalchemy import select, update

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.models import Chapter, ChapterRevision, Segment
from app.worker import Worker
from core.compare_cases import CASES
from helpers import enqueue, make_book, seed_book

pytestmark = pytest.mark.db


async def _chapter_with_pairs(book_id, pairs):
    """Chương 1 của truyện seed: mỗi cặp (mt, ai) là một câu; thêm một câu chỉ có bản Hachimi và một dòng meta."""
    async with get_sessionmaker()() as s:
        ch = (await s.scalars(select(Chapter).where(Chapter.book_id == book_id))).one()
        n = len(pairs)
        for i, (mt, ai) in enumerate(pairs):
            s.add(Segment(chapter_id=ch.id, idx=i, src=f"句{i}", is_meta=False, dst=ai, dst_machine=ai, dst_mt=mt, dst_ai=ai))
        s.add(Segment(chapter_id=ch.id, idx=n, src="只有", is_meta=False, dst="Chỉ MT", dst_machine="Chỉ MT", dst_mt="Chỉ MT"))
        s.add(Segment(chapter_id=ch.id, idx=n + 1, src="=====", is_meta=True, dst="=====", dst_machine="====="))
        await s.commit()
        return ch.id


async def test_detail_and_list_agree_on_differ_counts(api):
    # BR-6.11, AC-6.13, Review Focus 3
    book_id = await seed_book(statuses=["translated"])
    await _chapter_with_pairs(book_id, [(mt, ai) for mt, ai, _ in CASES])
    body = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()
    segs = body["segments"]
    for i, (_, _, expected) in enumerate(CASES):
        assert segs[i]["mt_ai_differ"] is expected, (i, CASES[i])
    n = len(CASES)
    assert (segs[n]["dst_mt"], segs[n]["dst_ai"], segs[n]["mt_ai_differ"]) == ("Chỉ MT", None, None)
    assert (segs[n + 1]["dst_mt"], segs[n + 1]["mt_ai_differ"]) == (None, None)
    differ = sum(1 for *_, d in CASES if d)
    expected = {"mt": n + 1, "ai": n, "both": n, "differ": differ}
    assert body["chapter"]["compare"] == expected
    listed = (await api.get(f"/api/v1/books/{book_id}/chapters")).json()["items"][0]
    assert listed["compare"] == expected


async def test_untranslated_and_new_chapters_have_empty_compare(api):
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    body = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()
    assert body["chapter"]["compare"] == {"mt": 0, "ai": 0, "both": 0, "differ": 0}
    assert all(s["dst_mt"] is None and s["dst_ai"] is None and s["mt_ai_differ"] is None for s in body["segments"])
    assert (await api.get(f"/api/v1/books/{book_id}/chapters")).json()["items"][0]["compare"]["both"] == 0


async def test_use_other_version_is_a_manual_edit_and_current_version_is_not(api):
    # BR-6.14, AC-6.16 (phần API): "Dùng bản này" = PATCH dst
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    await enqueue(book_id)
    assert await Worker(FakeTranslator).run_once()
    cid = chapters[0].id
    async with get_sessionmaker()() as s:
        await s.execute(update(Segment).where(Segment.chapter_id == cid, Segment.idx == 3).values(dst_ai="Bản AI."))
        await s.commit()
    seg = (await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "Bản AI."})).json()["segment"]
    assert (seg["dst"], seg["edited"], seg["dst_ai"], seg["mt_ai_differ"]) == ("Bản AI.", True, "Bản AI.", True)
    assert seg["dst_mt"] == seg["dst_machine"]
    seg = (await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": seg["dst_mt"]})).json()["segment"]
    assert seg["edited"] is False and seg["dst"] == seg["dst_mt"]
    async with get_sessionmaker()() as s:
        kinds = (await s.scalars(select(ChapterRevision.kind).where(ChapterRevision.chapter_id == cid)
                                 .order_by(ChapterRevision.created_at))).all()
    assert kinds == ["machine", "manual"]  # BR-6.1: hai lần sửa trong 5 phút gộp một revision


async def test_busy_chapter_rejects_use_version(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    await enqueue(book_id)
    assert await Worker(FakeTranslator).run_once()
    async with get_sessionmaker()() as s:
        await s.execute(update(Chapter).where(Chapter.id == chapters[0].id).values(status="queued"))
        await s.commit()
    r = await api.patch(f"/api/v1/segments/{chapters[0].id}/0", json={"dst": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHAPTER_BUSY"
