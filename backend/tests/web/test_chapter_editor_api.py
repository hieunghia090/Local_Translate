from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.models import Chapter, ChapterRevision, Job
from app.worker import Worker
from helpers import enqueue, make_book

pytestmark = pytest.mark.db


async def translated_book(api, n=2, lines=2):
    book_id, chapters = await make_book(api, n_chapters=n, lines=lines)
    await enqueue(book_id)
    worker = Worker(FakeTranslator)
    while await worker.run_once():
        pass
    return book_id, chapters


async def test_read_untranslated_chapter_shows_source(api):
    # AC-6.1 (phần API): dòng meta rõ ràng, câu chưa dịch dst = null
    book_id, _ = await make_book(api, n_chapters=3, lines=1)
    body = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/2")).json()
    segs = body["segments"]
    assert segs[0] == {"idx": 0, "is_meta": False, "src": "第2章 标题2", "dst": None, "dst_machine": None,
                       "edited": False, "flags": [], "honorific_edits": [], "glossary_spans": [],
                       "dst_mt": None, "dst_ai": None, "mt_ai_differ": None}
    assert segs[1]["is_meta"] and segs[1]["dst"] == "========="
    assert (body["prev_no"], body["next_no"], body["job"], body["source_missing"]) == (1, 3, None, False)
    assert body["chapter"]["run_config"]["beam"] == 2 and body["chapter"]["run_config_override"] is None


async def test_first_and_last_chapter_navigation(api):
    # BR-6.9, AC-6.8
    book_id, _ = await make_book(api, n_chapters=2, lines=1)
    assert (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()["prev_no"] is None
    assert (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/2")).json()["next_no"] is None
    r = await api.get(f"/api/v1/books/{book_id}/chapters/by-no/9")
    assert r.status_code == 404 and r.json()["error"]["code"] == "CHAPTER_NOT_FOUND"


async def test_edit_segment_persists_and_records_revision(api):
    # AC-6.2, BR-6.1
    book_id, chapters = await translated_book(api)
    cid = chapters[0].id
    r = await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "Câu tôi sửa"})
    assert r.status_code == 200
    assert r.json()["segment"]["dst"] == "Câu tôi sửa" and r.json()["segment"]["edited"] is True
    again = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()
    assert again["segments"][3]["dst"] == "Câu tôi sửa" and again["segments"][3]["edited"]
    async with get_sessionmaker()() as s:
        revs = (await s.scalars(select(ChapterRevision).where(ChapterRevision.chapter_id == cid)
                                .order_by(ChapterRevision.created_at))).all()
    assert [r.kind for r in revs] == ["machine", "manual"]
    assert revs[1].changed_idx == [3] and revs[1].snapshot[1]["dst"] == "Câu tôi sửa"


async def test_edits_within_five_minutes_merge(api):
    book_id, chapters = await translated_book(api)
    cid = chapters[0].id
    await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "A"})
    await api.patch(f"/api/v1/segments/{cid}/4", json={"dst": "B"})
    await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "A2"})
    async with get_sessionmaker()() as s:
        manual = (await s.scalars(select(ChapterRevision).where(ChapterRevision.kind == "manual"))).all()
        assert len(manual) == 1 and manual[0].changed_idx == [3, 4] and manual[0].segments_changed == 2
        manual[0].created_at = manual[0].created_at - timedelta(minutes=6)
        await s.commit()
    await api.patch(f"/api/v1/segments/{cid}/4", json={"dst": "B2"})
    async with get_sessionmaker()() as s:
        assert len((await s.scalars(select(ChapterRevision).where(ChapterRevision.kind == "manual"))).all()) == 2


async def test_editing_reviewed_chapter_moves_it_to_needs_review(api):
    # AC-6.3, BR-0.2
    book_id, chapters = await translated_book(api)
    cid = chapters[0].id
    assert (await api.post(f"/api/v1/chapters/{cid}/mark-reviewed")).json()["status"] == "reviewed"
    r = await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "Sửa sau khi soát"})
    assert r.json()["chapter"]["status"] == "needs_review"


async def test_revert_to_machine(api):
    book_id, chapters = await translated_book(api)
    cid = chapters[0].id
    await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "Sửa"})
    seg = (await api.patch(f"/api/v1/segments/{cid}/3", json={"revert": True})).json()["segment"]
    assert seg["dst"] == seg["dst_machine"] == "VI<第1章第1句话。>" and seg["edited"] is False


async def test_meta_and_missing_segments(api):
    book_id, chapters = await translated_book(api)
    cid = chapters[0].id
    r = await api.patch(f"/api/v1/segments/{cid}/1", json={"dst": "x"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "SEGMENT_IS_META"
    r = await api.patch(f"/api/v1/segments/{cid}/99", json={"dst": "x"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "SEGMENT_NOT_FOUND"
    r = await api.patch(f"/api/v1/segments/{cid}/3", json={})
    assert r.status_code == 422


async def test_cannot_edit_while_queued(api):
    # Review Focus 1
    book_id, chapters = await translated_book(api)
    cid = chapters[0].id
    await api.post(f"/api/v1/chapters/{cid}/translate", json={"keep_manual_edits": False})
    r = await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHAPTER_BUSY"


async def test_mark_reviewed_only_when_translated(api):
    # BR-6.3, AC-6.6
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    r = await api.post(f"/api/v1/chapters/{chapters[0].id}/mark-reviewed")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHAPTER_NOT_REVIEWABLE"


async def test_translate_chapter_jumps_queue(api):
    # BR-6.4
    book_id, chapters = await make_book(api, n_chapters=4, lines=1)
    await enqueue(book_id, chapter_ids=[c.id for c in chapters[:3]])
    r = await api.post(f"/api/v1/chapters/{chapters[3].id}/translate", json={"priority": True})
    assert r.status_code == 202 and r.json()["job_id"]
    active = (await api.get("/api/v1/queue", params={"book_id": book_id})).json()["active"]
    assert [j["chapter_no"] for j in active] == [4, 1, 2, 3]


async def test_translate_chapter_requires_choice_when_edited(api):
    # BR-0.3, AC-6.4
    book_id, chapters = await translated_book(api)
    cid = chapters[0].id
    await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "Giữ tôi"})
    r = await api.post(f"/api/v1/chapters/{cid}/translate", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFIRM_MANUAL_EDITS"
    r = await api.post(f"/api/v1/chapters/{cid}/translate", json={"keep_manual_edits": True})
    assert r.status_code == 202
    async with get_sessionmaker()() as s:
        job = (await s.scalars(select(Job).where(Job.status == "queued"))).one()
    assert job.kind == "retranslate" and job.options == {"keep_manual_edits": True}


async def test_translate_busy_chapter_rejected(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    await enqueue(book_id)
    r = await api.post(f"/api/v1/chapters/{chapters[0].id}/translate", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHAPTER_BUSY"


async def test_chapter_run_config_override(api):
    # BR-6.5
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    cid = chapters[0].id
    r = await api.put(f"/api/v1/chapters/{cid}/run-config", json={"beam": 4})
    assert r.json()["run_config_override"] == {"beam": 4} and r.json()["run_config"]["beam"] == 4
    assert (await api.put(f"/api/v1/chapters/{cid}/run-config", json={"beam": 7})).status_code == 422
    r = await api.put(f"/api/v1/chapters/{cid}/run-config", content="null", headers={"content-type": "application/json"})
    assert r.json()["run_config_override"] is None


async def test_rename_and_move_chapter(api):
    # BR-6.8
    book_id, chapters = await make_book(api, n_chapters=4, lines=1)
    r = await api.patch(f"/api/v1/chapters/{chapters[0].id}", json={"title_vi": "  Tên mới  "})
    assert r.json()["title_vi"] == "Tên mới"
    await api.patch(f"/api/v1/chapters/{chapters[3].id}", json={"no": 1})
    order = [c["title_zh"] for c in (await api.get(f"/api/v1/books/{book_id}/chapters")).json()["items"]]
    assert order == ["第4章 标题4", "第1章 标题1", "第2章 标题2", "第3章 标题3"]
    await api.patch(f"/api/v1/chapters/{chapters[3].id}", json={"no": 99})
    order = [c["no"] for c in (await api.get(f"/api/v1/books/{book_id}/chapters")).json()["items"]]
    assert order == [1, 2, 3, 4]
    titles = [c["title_zh"] for c in (await api.get(f"/api/v1/books/{book_id}/chapters")).json()["items"]]
    assert titles[-1] == "第4章 标题4"


async def test_job_progress_visible_on_chapter(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    await enqueue(book_id)
    body = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()
    assert body["job"]["status"] == "queued" and body["job"]["progress"] == 0
