import uuid

import pytest
from sqlalchemy import select, update

from app.db import get_sessionmaker
from app.models import Book, Chapter, Job, JobSegment
from app.services import queue
from helpers import enqueue, make_book

pytestmark = pytest.mark.db


async def _chapters(book_id):
    async with get_sessionmaker()() as s:
        return list((await s.scalars(select(Chapter).where(Chapter.book_id == uuid.UUID(book_id)).order_by(Chapter.no))).all())


async def test_enqueue_snapshots_config_and_marks_chapters_queued(api):
    book_id, chapters = await make_book(api)
    job_ids = await enqueue(book_id)
    async with get_sessionmaker()() as s:
        jobs = (await s.scalars(select(Job).order_by(Job.position))).all()
        book = await s.get(Book, uuid.UUID(book_id))
    assert [j.id for j in jobs] == job_ids
    assert [j.chapter_id for j in jobs] == [c.id for c in chapters]  # theo no tăng dần
    assert all(j.engine == "ct2" and j.run_config == book.run_config and j.prev_chapter_status == "todo" for j in jobs)
    assert {c.status for c in await _chapters(book_id)} == {"queued"}


async def test_chapter_override_merged_into_job_config(api):
    book_id, chapters = await make_book(api, n_chapters=1)
    async with get_sessionmaker()() as s:
        await s.execute(update(Chapter).where(Chapter.id == chapters[0].id).values(run_config_override={"beam": 4}))
        await s.commit()
    await enqueue(book_id)
    async with get_sessionmaker()() as s:
        job = (await s.scalars(select(Job))).one()
    assert job.run_config["beam"] == 4


async def test_queue_view(api):
    book_id, _ = await make_book(api)
    await enqueue(book_id)
    body = (await api.get("/api/v1/queue", params={"book_id": book_id})).json()
    assert body["paused"] == {"ct2": False, "deepseek": False}
    assert [j["chapter_no"] for j in body["active"]] == [1, 2, 3]
    assert body["active"][0]["chapter_title"].startswith("Chương 1")
    assert body["recent"] == [] and body["running_elsewhere"] is None and body["eta_seconds"] is None


async def test_running_elsewhere_reported(api):
    a, _ = await make_book(api, title="甲")
    b, _ = await make_book(api, title="乙")
    ids = await enqueue(a)
    await enqueue(b)
    async with get_sessionmaker()() as s:
        await s.execute(update(Job).where(Job.id == ids[0]).values(status="running"))
        await s.commit()
    body = (await api.get("/api/v1/queue", params={"book_id": b})).json()
    assert body["running_elsewhere"] == {"book_id": a, "title": "甲"}


async def test_pause_and_resume(api):
    book_id, _ = await make_book(api, n_chapters=1)
    ids = await enqueue(book_id)
    assert (await api.post("/api/v1/queue/pause")).json() == {"engine": "ct2", "paused": True}
    async with get_sessionmaker()() as s:
        await s.execute(update(Job).where(Job.id == ids[0]).values(status="paused"))
        await s.commit()
    assert (await api.post("/api/v1/queue/resume", json={"engine": "ct2"})).json() == {"engine": "ct2", "paused": False}
    async with get_sessionmaker()() as s:
        assert (await s.get(Job, ids[0])).status == "queued"


async def test_move_job(api):
    book_id, _ = await make_book(api, n_chapters=3)
    ids = await enqueue(book_id)
    r = await api.patch(f"/api/v1/jobs/{ids[2]}", json={"position": 0})
    assert r.status_code == 200
    order = [j["id"] for j in (await api.get("/api/v1/queue", params={"book_id": book_id})).json()["active"]]
    assert order == [str(ids[2]), str(ids[0]), str(ids[1])]
    r = await api.patch(f"/api/v1/jobs/{ids[2]}", json={"position": 99})
    order = [j["id"] for j in (await api.get("/api/v1/queue", params={"book_id": book_id})).json()["active"]]
    assert order[-1] == str(ids[2])


async def test_cannot_move_running_job(api):
    book_id, _ = await make_book(api, n_chapters=1)
    ids = await enqueue(book_id)
    async with get_sessionmaker()() as s:
        await s.execute(update(Job).where(Job.id == ids[0]).values(status="running"))
        await s.commit()
    r = await api.patch(f"/api/v1/jobs/{ids[0]}", json={"position": 0})
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_NOT_MOVABLE"


async def test_cancel_queued_restores_previous_status(api):
    # BR-3.10
    book_id, chapters = await make_book(api, n_chapters=1)
    async with get_sessionmaker()() as s:
        await s.execute(update(Chapter).where(Chapter.id == chapters[0].id).values(status="error"))
        await s.commit()
    ids = await enqueue(book_id)
    r = await api.delete(f"/api/v1/jobs/{ids[0]}")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (await _chapters(book_id))[0].status == "error"


async def test_cancel_paused_drops_staged_results(api):
    book_id, _ = await make_book(api, n_chapters=1)
    ids = await enqueue(book_id)
    async with get_sessionmaker()() as s:
        await s.execute(update(Job).where(Job.id == ids[0]).values(status="paused"))
        s.add(JobSegment(job_id=ids[0], idx=0, dst="x", flags=[]))
        await s.commit()
    await api.delete(f"/api/v1/jobs/{ids[0]}")
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(JobSegment))).all() == []


async def test_cancel_running_only_flags_job(api):
    book_id, _ = await make_book(api, n_chapters=1)
    ids = await enqueue(book_id)
    async with get_sessionmaker()() as s:
        await s.execute(update(Job).where(Job.id == ids[0]).values(status="running"))
        await s.execute(update(Chapter).values(status="translating"))
        await s.commit()
    r = await api.delete(f"/api/v1/jobs/{ids[0]}")
    assert r.json()["status"] == "cancelled"
    assert (await _chapters(book_id))[0].status == "translating"  # worker sẽ trả chương về như cũ


async def test_cancel_errors(api):
    assert (await api.delete(f"/api/v1/jobs/{uuid.uuid4()}")).json()["error"]["code"] == "JOB_NOT_FOUND"
    book_id, _ = await make_book(api, n_chapters=1)
    ids = await enqueue(book_id)
    await api.delete(f"/api/v1/jobs/{ids[0]}")
    r = await api.delete(f"/api/v1/jobs/{ids[0]}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "JOB_FINISHED"


async def test_recover_interrupted(api):
    # NFR-3: job đang chạy khi worker chết thì quay lại hàng đợi
    book_id, _ = await make_book(api, n_chapters=1)
    ids = await enqueue(book_id)
    async with get_sessionmaker()() as s:
        await s.execute(update(Job).where(Job.id == ids[0]).values(status="running"))
        await s.execute(update(Chapter).values(status="translating"))
        await s.commit()
        assert await queue.recover_interrupted(s, "ct2") == 1
        await s.commit()
        assert (await s.get(Job, ids[0])).status == "queued"
    assert (await _chapters(book_id))[0].status == "queued"


async def test_enqueue_writes_system_log(api):
    book_id, _ = await make_book(api, n_chapters=2)
    await enqueue(book_id)
    items = (await api.get(f"/api/v1/books/{book_id}/logs", params={"source": "system"})).json()["items"]
    assert items[0]["message"] == "Thêm 2 chương vào hàng đợi"
