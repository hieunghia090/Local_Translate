import uuid

import pytest
from sqlalchemy import update

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.models import Segment
from app.worker import Worker
from helpers import enqueue, make_book

pytestmark = pytest.mark.db


async def translated(api, lines=2):
    book_id, chapters = await make_book(api, n_chapters=1, lines=lines)
    await enqueue(book_id)
    await Worker(FakeTranslator).run_once()
    return book_id, chapters[0].id


async def test_history_newest_first(api):
    # BR-6.7
    book_id, cid = await translated(api)
    await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "Sửa"})
    revs = (await api.get(f"/api/v1/chapters/{cid}/revisions")).json()
    assert [r["kind"] for r in revs] == ["manual", "machine"]
    assert revs[1]["beam"] == 2 and revs[1]["model_id"] == "fake" and revs[1]["restorable"]
    assert revs[0]["segments_changed"] == 1


async def test_restore_machine_revision_undoes_edits_and_adds_revision(api):
    book_id, cid = await translated(api)
    machine_id = (await api.get(f"/api/v1/chapters/{cid}/revisions")).json()[0]["id"]
    await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "Sửa"})
    r = await api.post(f"/api/v1/chapters/{cid}/revisions/{machine_id}/restore")
    assert r.status_code == 200 and r.json()["restored"] == 1 and r.json()["skipped"] == 0
    seg = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()["segments"][3]
    assert seg["dst"] == "VI<第1章第1句话。>" and seg["edited"] is False
    revs = (await api.get(f"/api/v1/chapters/{cid}/revisions")).json()
    assert len(revs) == 3 and revs[0]["note"].startswith("Khôi phục bản")  # không xoá gì


async def test_restore_skips_lines_whose_source_changed(api):
    # Review Focus 3
    book_id, cid = await translated(api)
    machine_id = (await api.get(f"/api/v1/chapters/{cid}/revisions")).json()[0]["id"]
    async with get_sessionmaker()() as s:
        await s.execute(update(Segment).where(Segment.chapter_id == cid, Segment.idx == 4)
                        .values(src="khác", dst="Mới", edited=True))
        await s.commit()
    r = await api.post(f"/api/v1/chapters/{cid}/revisions/{machine_id}/restore")
    assert r.json()["skipped"] == 1
    seg = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()["segments"][4]
    assert seg["dst"] == "Mới"


async def test_restore_moves_reviewed_chapter_back_to_needs_review(api):
    book_id, cid = await translated(api)
    machine_id = (await api.get(f"/api/v1/chapters/{cid}/revisions")).json()[0]["id"]
    await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "Sửa"})
    await api.post(f"/api/v1/chapters/{cid}/mark-reviewed")
    r = await api.post(f"/api/v1/chapters/{cid}/revisions/{machine_id}/restore")
    assert r.json()["restored"] == 1
    body = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()
    assert body["chapter"]["status"] == "needs_review" and body["segments"][3]["dst"] == "VI<第1章第1句话。>"


async def test_restore_errors(api):
    book_id, cid = await translated(api)
    r = await api.post(f"/api/v1/chapters/{cid}/revisions/{uuid.uuid4()}/restore")
    assert r.status_code == 404 and r.json()["error"]["code"] == "REVISION_NOT_FOUND"
    other_book, other_cid = await translated(api)
    other_rev = (await api.get(f"/api/v1/chapters/{other_cid}/revisions")).json()[0]["id"]
    r = await api.post(f"/api/v1/chapters/{cid}/revisions/{other_rev}/restore")
    assert r.status_code == 404


async def test_restore_with_nothing_to_restore_creates_no_revision(api):
    book_id, cid = await translated(api)
    machine_id = (await api.get(f"/api/v1/chapters/{cid}/revisions")).json()[0]["id"]
    await api.post(f"/api/v1/chapters/{cid}/mark-reviewed")
    r = await api.post(f"/api/v1/chapters/{cid}/revisions/{machine_id}/restore")  # đang giống hệt bản máy
    assert r.status_code == 200
    body = r.json()
    assert body["restored"] == 0 and body["revision_id"] is None and body["skipped"] == 0
    assert len((await api.get(f"/api/v1/chapters/{cid}/revisions")).json()) == 1
    chapter = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()["chapter"]
    assert chapter["status"] == "reviewed"
