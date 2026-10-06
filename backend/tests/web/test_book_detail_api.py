import shutil
import uuid

import pytest
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import Book, Job, LogEntry
from app.services import books as book_service
from app.services import logs
from helpers import enqueue, make_book, seed_book

pytestmark = pytest.mark.db


@pytest.fixture
def trash(tmp_path, monkeypatch):
    bin_dir = tmp_path / "trash"
    bin_dir.mkdir()
    moved = []

    def fake_trash(path):
        moved.append(path.name)
        shutil.move(str(path), str(bin_dir / path.name))

    monkeypatch.setattr(book_service, "move_to_trash", fake_trash)
    return moved


async def test_detail_with_stats(api):
    # AC-3.1
    book_id = await seed_book(title_zh="大宋有种", title_vi="Đại Tống", statuses=["translated"] * 90 + ["todo"] * 617)
    body = (await api.get(f"/api/v1/books/{book_id}")).json()
    assert body["stats"]["total"] == 707 and body["stats"]["translated"] == 90
    assert body["progress_pct"] == 13 and body["state"] == "in_progress"
    assert body["total_chars"] == 707_000
    assert body["run_config"]["engine"] == "ct2"


async def test_detail_unknown(api):
    r = await api.get(f"/api/v1/books/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "BOOK_NOT_FOUND"


async def test_patch_info_and_partial_run_config(api):
    book_id = await seed_book(genre="xianxia")
    r = await api.patch(f"/api/v1/books/{book_id}", json={"title_vi": "Tên mới", "author": "  ", "run_config": {"beam": 4}})
    assert r.status_code == 200
    body = r.json()
    assert body["title_vi"] == "Tên mới" and body["author"] is None
    assert body["run_config"]["beam"] == 4 and body["run_config"]["chunk_mode"] == "paragraph"


async def test_genre_change_keeps_honorific(api):
    # BR-3.14
    book_id = await seed_book(genre="xianxia")
    body = (await api.patch(f"/api/v1/books/{book_id}", json={"genre": "urban"})).json()
    assert body["genre"] == "urban"
    assert body["run_config"]["honorific"] == {"kinship": True, "pronoun": True, "modern_stable": False}


@pytest.mark.parametrize("patch", [{"run_config": {"beam": 9}}, {"title_zh": "   "}, {"genre": "sci-fi"}, {"bogus": 1}])
async def test_patch_rejects_bad_input(api, patch):
    book_id = await seed_book()
    r = await api.patch(f"/api/v1/books/{book_id}", json=patch)
    assert r.status_code == 422


async def test_delete_requires_exact_title(api, trash):
    # AC-3.9 (phần API)
    book_id, _ = await make_book(api, n_chapters=1, title="书名")
    r = await api.delete(f"/api/v1/books/{book_id}", params={"confirm": "sai tên"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "CONFIRM_MISMATCH"
    assert trash == []


async def test_delete_moves_folder_to_trash_and_removes_everything(api, trash, data_dir):
    book_id, _ = await make_book(api, n_chapters=2, title="书名")
    await enqueue(book_id)
    r = await api.delete(f"/api/v1/books/{book_id}", params={"confirm": " 书名 "})
    assert r.status_code == 204
    assert (await api.get(f"/api/v1/books/{book_id}")).status_code == 404
    assert (await api.get("/api/v1/books")).json()["items"] == []
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(Job)) == 0
        assert await s.scalar(select(func.count()).select_from(LogEntry).where(LogEntry.book_id == uuid.UUID(book_id))) == 0
    assert len(trash) == 1
    assert not (data_dir / "books" / trash[0]).exists()


async def test_delete_while_worker_runs_job(api, trash):
    # Review Focus 4
    from app.core.translator import FakeTranslator
    from app.worker import Worker

    book_id, _ = await make_book(api, n_chapters=1, lines=4, title="书名")
    await enqueue(book_id)

    async def delete_mid_job(step_no):
        if step_no == 1:
            r = await api.delete(f"/api/v1/books/{book_id}", params={"confirm": "书名"})
            assert r.status_code == 204

    await Worker(FakeTranslator, lines_per_step=2, on_step=delete_mid_job).run_once()
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(Book)) == 0


async def test_delete_book_succeeds_even_if_trashing_fails(api, monkeypatch):
    def boom(path):
        raise OSError("không đưa vào thùng rác được")

    monkeypatch.setattr(book_service, "move_to_trash", boom)
    book_id, _ = await make_book(api, n_chapters=1, title="书名")
    r = await api.delete(f"/api/v1/books/{book_id}", params={"confirm": "书名"})
    assert r.status_code == 204
    assert (await api.get(f"/api/v1/books/{book_id}")).status_code == 404
