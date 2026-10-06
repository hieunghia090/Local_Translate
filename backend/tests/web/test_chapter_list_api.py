import uuid

import pytest
from sqlalchemy import select, update

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.models import Chapter, Job, Segment
from app.worker import Worker
from helpers import enqueue, make_book, seed_book

pytestmark = pytest.mark.db


async def _set_status(chapter_ids, status):
    async with get_sessionmaker()() as s:
        await s.execute(update(Chapter).where(Chapter.id.in_(chapter_ids)).values(status=status))
        await s.commit()


async def test_filter_by_status_and_counts(api):
    # AC-3.2
    book_id = await seed_book(statuses=["todo", "error", "translated", "error", "reviewed"])
    r = await api.get(f"/api/v1/books/{book_id}/chapters", params={"status": "error"})
    body = r.json()
    assert [c["no"] for c in body["items"]] == [2, 4] and body["total"] == 2
    both = (await api.get(f"/api/v1/books/{book_id}/chapters", params={"status": "translated,reviewed"})).json()
    assert [c["no"] for c in both["items"]] == [3, 5]


async def test_search_by_number_and_titles(api):
    book_id, chapters = await make_book(api, n_chapters=12, lines=1)
    async with get_sessionmaker()() as s:
        await s.execute(update(Chapter).where(Chapter.id == chapters[4].id).values(title_vi="Chương 5: Đại chiến"))
        await s.commit()
    assert [c["no"] for c in (await api.get(f"/api/v1/books/{book_id}/chapters", params={"q": "12"})).json()["items"]] == [12]
    assert [c["no"] for c in (await api.get(f"/api/v1/books/{book_id}/chapters", params={"q": "dai chien"})).json()["items"]] == [5]
    assert [c["no"] for c in (await api.get(f"/api/v1/books/{book_id}/chapters", params={"q": "标题7"})).json()["items"]] == [7]


async def test_cursor_pagination(api):
    book_id = await seed_book(statuses=["todo"] * 7)
    first = (await api.get(f"/api/v1/books/{book_id}/chapters", params={"limit": 3})).json()
    assert [c["no"] for c in first["items"]] == [1, 2, 3] and first["next_cursor"] == "3" and first["total"] == 7
    last = (await api.get(f"/api/v1/books/{book_id}/chapters", params={"limit": 5, "cursor": 5})).json()
    assert [c["no"] for c in last["items"]] == [6, 7] and last["next_cursor"] is None


@pytest.mark.parametrize("params", [{"status": "done"}, {"limit": 0}, {"limit": 201}, {"cursor": "abc"}])
async def test_bad_list_params(api, params):
    # Review Focus 5
    book_id = await seed_book(statuses=["todo"])
    r = await api.get(f"/api/v1/books/{book_id}/chapters", params=params)
    assert r.status_code == 422


async def test_rows_show_model_last_run_and_manual_edits(api):
    book_id, chapters = await make_book(api, n_chapters=2, lines=1)
    await enqueue(book_id, chapter_ids=[chapters[0].id])
    await Worker(FakeTranslator).run_once()
    async with get_sessionmaker()() as s:
        await s.execute(update(Segment).where(Segment.chapter_id == chapters[0].id, Segment.idx == 3).values(edited=True))
        await s.commit()
    items = (await api.get(f"/api/v1/books/{book_id}/chapters")).json()["items"]
    assert items[0]["model_id"] == "fake" and items[0]["last_run"]["beam"] == 2 and items[0]["has_manual_edits"]
    assert items[1]["model_id"] is None and items[1]["last_run"] is None and not items[1]["has_manual_edits"]


async def test_bulk_translate_skips_already_translated(api):
    # AC-3.3, BR-3.1
    book_id, chapters = await make_book(api, n_chapters=5, lines=1)
    await _set_status([chapters[1].id, chapters[3].id], "translated")
    r = await api.post(f"/api/v1/books/{book_id}/chapters/bulk",
                       json={"action": "translate", "chapter_ids": [str(c.id) for c in chapters]})
    assert r.json()["affected"] == 3 and r.json()["skipped"] == 2 and len(r.json()["job_ids"]) == 3
    statuses = [c["status"] for c in (await api.get(f"/api/v1/books/{book_id}/chapters")).json()["items"]]
    assert statuses == ["queued", "translated", "queued", "translated", "queued"]


async def test_bulk_translate_all_untranslated_by_filter(api):
    # BR-3.4: theo thứ tự no
    book_id, chapters = await make_book(api, n_chapters=4, lines=1)
    await _set_status([chapters[2].id], "error")
    await _set_status([chapters[1].id], "translated")
    r = await api.post(f"/api/v1/books/{book_id}/chapters/bulk",
                       json={"action": "translate", "filter": {"status": ["todo", "error"]}})
    assert r.json()["affected"] == 3
    async with get_sessionmaker()() as s:
        jobs = (await s.scalars(select(Job).order_by(Job.position))).all()
    assert [j.chapter_id for j in jobs] == [chapters[0].id, chapters[2].id, chapters[3].id]


async def test_bulk_needs_target(api):
    book_id = await seed_book(statuses=["todo"])
    r = await api.post(f"/api/v1/books/{book_id}/chapters/bulk", json={"action": "translate"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "BULK_TARGET_REQUIRED"


async def test_bulk_retranslate_asks_about_manual_edits(api):
    # BR-0.3, BR-3.2
    book_id, chapters = await make_book(api, n_chapters=2, lines=1)
    await enqueue(book_id)
    worker = Worker(FakeTranslator)
    while await worker.run_once():
        pass
    async with get_sessionmaker()() as s:
        await s.execute(update(Segment).where(Segment.chapter_id == chapters[0].id, Segment.idx == 3).values(edited=True))
        await s.commit()
    ids = [str(c.id) for c in chapters]
    r = await api.post(f"/api/v1/books/{book_id}/chapters/bulk", json={"action": "retranslate", "chapter_ids": ids})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CONFIRM_MANUAL_EDITS"
    assert r.json()["error"]["details"]["chapters"] == 1
    r = await api.post(f"/api/v1/books/{book_id}/chapters/bulk",
                       json={"action": "retranslate", "chapter_ids": ids, "keep_manual_edits": True})
    assert r.json()["affected"] == 2
    async with get_sessionmaker()() as s:
        jobs = (await s.scalars(select(Job).where(Job.kind == "retranslate"))).all()
    assert len(jobs) == 2 and all(j.options == {"keep_manual_edits": True} for j in jobs)


async def test_queued_chapters_never_retranslated(api):
    # BR-3.6
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    await enqueue(book_id)
    r = await api.post(f"/api/v1/books/{book_id}/chapters/bulk",
                       json={"action": "retranslate", "chapter_ids": [str(chapters[0].id)]})
    assert r.json() == {"affected": 0, "skipped": 1, "job_ids": []}


async def test_bulk_mark_reviewed(api):
    # BR-3.3
    book_id = await seed_book(statuses=["translated", "needs_review", "todo", "error"])
    async with get_sessionmaker()() as s:
        ids = [str(c.id) for c in (await s.scalars(select(Chapter).where(Chapter.book_id == book_id))).all()]
    r = await api.post(f"/api/v1/books/{book_id}/chapters/bulk", json={"action": "mark_reviewed", "chapter_ids": ids})
    assert r.json()["affected"] == 2 and r.json()["skipped"] == 2
    items = (await api.get(f"/api/v1/books/{book_id}/chapters")).json()["items"]
    assert [c["status"] for c in items] == ["reviewed", "reviewed", "todo", "error"]
    assert items[0]["reviewed_at"] is not None


async def test_other_books_chapters_ignored(api):
    a = await seed_book(statuses=["todo"])
    b = await seed_book(title_zh="乙", statuses=["todo"])
    async with get_sessionmaker()() as s:
        foreign = (await s.scalars(select(Chapter).where(Chapter.book_id == b))).one()
    r = await api.post(f"/api/v1/books/{a}/chapters/bulk", json={"action": "translate", "chapter_ids": [str(foreign.id)]})
    assert r.json()["affected"] == 0


async def test_last_run_survives_replace_source(api):
    # BR-0.4 thêm revision machine có note: không được che lần chạy máy thật
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    await enqueue(book_id)
    await Worker(FakeTranslator).run_once()
    r = await api.put(f"/api/v1/chapters/{chapters[0].id}/source", json={"content": "第1章 新版\n" + "他走了很远的路，终于到了。" * 20})
    assert r.status_code == 200 and r.json()["changed"] is True
    row = (await api.get(f"/api/v1/books/{book_id}/chapters")).json()["items"][0]
    assert row["last_run"] is not None and row["last_run"]["beam"] == 2
