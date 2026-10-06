import asyncio
import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.services import events
from app.models import Book, Chapter, ChapterRevision, Segment
from app.worker import Worker
from helpers import chapter_text, enqueue, make_book

pytestmark = pytest.mark.db

BODY = "他走了很远的路，终于到了。" * 20


async def _chapters(book_id):
    async with get_sessionmaker()() as s:
        return list((await s.scalars(select(Chapter).where(Chapter.book_id == uuid.UUID(book_id)).order_by(Chapter.no))).all())


async def _slug(book_id):
    async with get_sessionmaker()() as s:
        return (await s.get(Book, uuid.UUID(book_id))).slug


async def test_add_chapter_json_at_end(api, data_dir):
    # US-3.7
    book_id, _ = await make_book(api, n_chapters=2, lines=1)
    r = await api.post(f"/api/v1/books/{book_id}/chapters", json={"title_zh": "第3章 新章", "content": BODY})
    assert r.status_code == 201
    row = r.json()["chapters"][0]
    assert row["no"] == 3 and row["title_zh"] == "第3章 新章" and row["status"] == "todo"
    assert row["title_vi"] == "Chương 3: VI<新章>"
    chapters = await _chapters(book_id)
    path = data_dir / "books" / await _slug(book_id) / "source" / chapters[2].source_file
    text = path.read_text(encoding="utf-8")
    assert text.startswith("第3章 新章\n") and chapters[2].char_count == 224
    assert chapters[2].source_hash == hashlib.sha256(path.read_bytes()).hexdigest()


async def test_insert_after_renumbers_following(api):
    # Review Focus 2
    book_id, _ = await make_book(api, n_chapters=3, lines=1)
    r = await api.post(f"/api/v1/books/{book_id}/chapters", json={"title_zh": "插曲", "content": BODY, "after_no": 1})
    assert r.json()["chapters"][0]["no"] == 2
    titles = [c.title_zh for c in await _chapters(book_id)]
    assert titles == ["第1章 标题1", "插曲", "第2章 标题2", "第3章 标题3"]
    assert [c.no for c in await _chapters(book_id)] == [1, 2, 3, 4]


async def test_add_chapters_multipart_in_file_order(api):
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    files = [("files[]", ("0003.txt", chapter_text(3, 1), "text/plain")),
             ("files[]", ("0002.txt", chapter_text(2, 1), "text/plain"))]
    r = await api.post(f"/api/v1/books/{book_id}/chapters", files=files)
    assert r.status_code == 201
    assert [c["title_zh"] for c in r.json()["chapters"]] == ["第2章 标题2", "第3章 标题3"]


async def test_add_chapter_errors(api):
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    r = await api.post(f"/api/v1/books/{book_id}/chapters", json={"content": "   "})
    assert r.status_code == 422 and r.json()["error"]["code"] == "EMPTY_CHAPTER"
    gbk = ("第2章 开始\n" + BODY).encode("gbk")
    r = await api.post(f"/api/v1/books/{book_id}/chapters", files=[("files[]", ("x.txt", gbk, "text/plain"))])
    assert r.status_code == 201  # auto nhận GBK
    r = await api.post(f"/api/v1/books/{book_id}/chapters", files=[("other", ("x", b"", "text/plain"))])
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_FILES"


async def test_replace_source_resets_and_keeps_old_translation(api, data_dir):
    # BR-0.4
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    await enqueue(book_id)
    await Worker(FakeTranslator).run_once()
    cid = chapters[0].id
    r = await api.put(f"/api/v1/chapters/{cid}/source", json={"content": "第1章 新版\n" + BODY})
    assert r.status_code == 200 and r.json()["changed"] is True
    ch = (await _chapters(book_id))[0]
    assert (ch.status, ch.model_id, ch.title_zh) == ("todo", None, "第1章 新版")
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(Segment).where(Segment.chapter_id == cid))).all() == []
        kept = (await s.scalars(select(ChapterRevision).where(ChapterRevision.note == "Bản dịch trước khi thay bản gốc"))).one()
    assert kept.snapshot[0]["dst"] == "VI<第1章 标题1>"
    path = data_dir / "books" / await _slug(book_id) / "source" / ch.source_file
    assert path.read_text(encoding="utf-8").startswith("第1章 新版")


async def test_replace_with_same_content_is_noop(api, data_dir):
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    path = data_dir / "books" / await _slug(book_id) / "source" / chapters[0].source_file
    files = {"file": ("x.txt", path.read_bytes(), "text/plain")}
    r = await api.put(f"/api/v1/chapters/{chapters[0].id}/source", files=files)
    assert r.json()["changed"] is False


async def test_delete_chapter_renumbers_and_removes_file(api, data_dir):
    book_id, chapters = await make_book(api, n_chapters=3, lines=1)
    path = data_dir / "books" / await _slug(book_id) / "source" / chapters[1].source_file
    assert (await api.delete(f"/api/v1/chapters/{chapters[1].id}")).status_code == 204
    left = await _chapters(book_id)
    assert [(c.no, c.title_zh) for c in left] == [(1, "第1章 标题1"), (2, "第3章 标题3")]
    assert not path.exists()


async def test_worker_finds_source_after_renumber(api):
    # Review Focus 2: source_file không đổi khi đánh số lại
    book_id, chapters = await make_book(api, n_chapters=3, lines=1)
    await api.delete(f"/api/v1/chapters/{chapters[0].id}")
    await enqueue(book_id, chapter_ids=[chapters[2].id])
    fake = FakeTranslator()
    await Worker(lambda: fake).run_once()
    assert fake.calls[0][0] == "第3章 标题3"


async def test_busy_chapters_protected(api):
    # BR-3.6
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    await enqueue(book_id)
    cid = chapters[0].id
    assert (await api.delete(f"/api/v1/chapters/{cid}")).json()["error"]["code"] == "CHAPTER_BUSY"
    r = await api.put(f"/api/v1/chapters/{cid}/source", json={"content": "第1章 x\n" + BODY})
    assert r.json()["error"]["code"] == "CHAPTER_BUSY"


async def test_concurrent_renumbering_keeps_numbers_contiguous(api):
    # Đánh số lại phải được tuần tự hoá theo truyện: không trùng/lỗ số
    book_id, chapters = await make_book(api, n_chapters=6, lines=1)
    ids = [c.id for c in chapters]
    results = await asyncio.gather(
        api.patch(f"/api/v1/chapters/{ids[0]}", json={"no": 6}),
        api.patch(f"/api/v1/chapters/{ids[5]}", json={"no": 1}),
        api.delete(f"/api/v1/chapters/{ids[2]}"),
        api.post(f"/api/v1/books/{book_id}/chapters", json={"title_zh": "第7章 新", "content": BODY}),
    )
    assert all(200 <= r.status_code < 300 for r in results), [(r.status_code, r.text) for r in results]
    assert [c.no for c in await _chapters(book_id)] == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("content", [b"", b"khong phai json"])
async def test_malformed_json_body_is_422(api, content):
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    hdr = {"content-type": "application/json"}
    r = await api.post(f"/api/v1/books/{book_id}/chapters", content=content, headers=hdr)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    r = await api.put(f"/api/v1/chapters/{chapters[0].id}/source", content=content, headers=hdr)
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("after_no", ["²", "abc", "-1", "1.5"])
async def test_multipart_bad_after_no_is_422(api, after_no):
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    r = await api.post(f"/api/v1/books/{book_id}/chapters", data={"after_no": after_no},
                       files=[("files[]", ("0002.txt", chapter_text(2, 1), "text/plain"))])
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("after_no,expected_no", [("", 2), ("0", 1)])
async def test_multipart_after_no_empty_appends_and_zero_prepends(api, after_no, expected_no):
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    r = await api.post(f"/api/v1/books/{book_id}/chapters", data={"after_no": after_no},
                       files=[("files[]", ("0002.txt", chapter_text(2, 1), "text/plain"))])
    assert r.status_code == 201 and r.json()["chapters"][0]["no"] == expected_no


async def test_int32_overflow_is_422_not_500(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    big = 10**12
    cid = chapters[0].id
    for r in [
        await api.get(f"/api/v1/books/{book_id}/chapters", params={"cursor": big}),
        await api.get(f"/api/v1/books/{book_id}/chapters/by-no/{big}"),
        await api.patch(f"/api/v1/segments/{cid}/{big}", json={"dst": "x"}),
        await api.patch(f"/api/v1/chapters/{cid}", json={"no": big}),
        await api.post(f"/api/v1/books/{book_id}/chapters", json={"content": BODY, "after_no": big}),
    ]:
        assert r.status_code == 422, (r.request.url, r.status_code)


async def _drain(q, wait=0.5):
    got = []
    try:
        while True:
            got.append(await asyncio.wait_for(q.get(), wait))
    except TimeoutError:
        return got


async def _collect(book_id, action):
    broker = events.EventBroker()
    q = await broker.subscribe(book_id)
    try:
        await action()
        return [e for e in await _drain(q) if e["type"] == "chapter.updated"]
    finally:
        await broker.close()


async def test_delete_chapter_publishes_deleted_and_renumbered_events(api):
    book_id, chapters = await make_book(api, n_chapters=3, lines=1)

    async def act():
        assert (await api.delete(f"/api/v1/chapters/{chapters[0].id}")).status_code == 204

    got = [e["data"] for e in await _collect(book_id, act)]
    assert {"id": str(chapters[0].id), "deleted": True} in got
    assert {"renumbered": True} in got


async def test_move_and_insert_publish_renumbered_but_append_does_not(api):
    book_id, chapters = await make_book(api, n_chapters=3, lines=1)

    async def move():
        assert (await api.patch(f"/api/v1/chapters/{chapters[0].id}", json={"no": 3})).status_code == 200

    async def insert():
        r = await api.post(f"/api/v1/books/{book_id}/chapters", json={"content": BODY, "after_no": 1})
        assert r.status_code == 201

    async def append():
        assert (await api.post(f"/api/v1/books/{book_id}/chapters", json={"content": BODY})).status_code == 201

    assert {"renumbered": True} in [e["data"] for e in await _collect(book_id, move)]
    assert {"renumbered": True} in [e["data"] for e in await _collect(book_id, insert)]
    assert {"renumbered": True} not in [e["data"] for e in await _collect(book_id, append)]


async def test_replace_source_failure_leaves_original_file_and_no_tmp(api, data_dir, monkeypatch):
    from app.services import chapters as chapter_service

    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    source_dir = data_dir / "books" / await _slug(book_id) / "source"
    before = {p.name: p.read_bytes() for p in source_dir.iterdir()}

    async def boom(*a, **k):
        raise RuntimeError("lỗi trước commit")

    monkeypatch.setattr(chapter_service.events, "emit_book_stats", boom)
    with pytest.raises(RuntimeError):  # ASGITransport ném lại lỗi server
        await api.put(f"/api/v1/chapters/{chapters[0].id}/source", json={"content": "第1章 新版\n" + BODY})
    assert {p.name: p.read_bytes() for p in source_dir.iterdir()} == before


async def test_add_chapters_failure_removes_written_files(api, data_dir, monkeypatch):
    from app.services import chapters as chapter_service

    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    source_dir = data_dir / "books" / await _slug(book_id) / "source"
    before = sorted(p.name for p in source_dir.iterdir())

    async def boom(*a, **k):
        raise RuntimeError("lỗi sau khi ghi file")

    monkeypatch.setattr(chapter_service.logs, "write_log", boom)
    with pytest.raises(RuntimeError):
        await api.post(f"/api/v1/books/{book_id}/chapters", json={"content": BODY})
    assert sorted(p.name for p in source_dir.iterdir()) == before
    assert len(await _chapters(book_id)) == 1
