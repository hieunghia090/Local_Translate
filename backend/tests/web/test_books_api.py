import hashlib
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.main import create_app
from app.models import Book, Chapter, ImportSession, Job
from app.services import books as books_service
from app.services import imports
from helpers import upload

pytestmark = pytest.mark.db

BODY = "他走了很远的路，终于到了。" * 20


def chapter(title: str) -> bytes:
    return f"{title}\n=========\nNguồn: https://x\n\n{BODY}\n".encode()


THREE = [
    ("0001 - a.txt", chapter("第1章 标题一")),
    ("0002 - b.txt", chapter("第2章 标题二")),
    ("0003 - c.txt", chapter("第3章 标题三")),
]


async def _book(book_id: str) -> tuple[Book, list[Chapter]]:
    async with get_sessionmaker()() as s:
        book = await s.get(Book, uuid.UUID(book_id))
        chapters = (await s.scalars(select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.no))).all()
        return book, list(chapters)


async def test_create_from_import(api, data_dir):
    # AC-2.5, AC-2.8, AC-2.9
    view = await upload(api, THREE)
    await api.patch(
        f"/api/v1/imports/{view['import_id']}",
        json={"chapters": [{"key": "c0002", "selected": False}, {"key": "c0003", "title_vi": "Tên tự sửa"}]},
    )
    r = await api.post(
        "/api/v1/books",
        json={"title_zh": "大宋有种", "title_vi": "Đại Tống Hữu Chủng", "genre": "modern_war",
              "import_id": view["import_id"]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["slug"] == "dai-tong-huu-chung"

    book, chapters = await _book(r.json()["id"])
    assert book.run_config["honorific"] == {"kinship": False, "pronoun": False, "modern_stable": True}
    assert [(c.no, c.title_vi, c.status) for c in chapters] == [
        (1, "Chương 1: VI<标题一>", "todo"), (2, "Tên tự sửa", "todo"),
    ]
    source = data_dir / "books" / "dai-tong-huu-chung" / "source"
    assert sorted(p.name for p in source.iterdir()) == ["0001.txt", "0002.txt"]
    data = (source / "0002.txt").read_bytes()
    assert data.decode("utf-8").startswith("第3章 标题三\n=========")
    assert chapters[1].source_hash == hashlib.sha256(data).hexdigest()
    assert chapters[1].char_count == view["chapters"][2]["chars"]
    assert [c.source_file for c in chapters] == ["0001.txt", "0002.txt"]

    # phiên import đã dùng thì bị xoá
    assert (await api.get(f"/api/v1/imports/{view['import_id']}")).status_code == 404
    assert not imports.import_dir(uuid.UUID(view["import_id"])).exists()


async def test_blank_title_vi_is_translated_without_ads(api, fake_translator):
    r = await api.post("/api/v1/books", json={"title_zh": "大宋有种（求收藏）"})
    book, _ = await _book(r.json()["id"])
    assert book.title_vi == "VI<大宋有种>"
    assert fake_translator.calls[-1] == ["大宋有种"]


async def test_default_titles_renumbered_when_title_has_no_number(api):
    files = [("1.txt", chapter("楔子甲")), ("2.txt", chapter("番外乙")), ("3.txt", chapter("番外丙"))]
    view = await upload(api, files)
    await api.patch(f"/api/v1/imports/{view['import_id']}", json={"chapters": [{"key": "c0002", "selected": False}]})
    r = await api.post("/api/v1/books", json={"title_zh": "书", "import_id": view["import_id"]})
    _, chapters = await _book(r.json()["id"])
    assert [c.title_vi for c in chapters] == ["Chương 1: VI<楔子甲>", "Chương 2: VI<番外丙>"]


async def test_empty_book(api, data_dir):
    # US-2.3
    r = await api.post("/api/v1/books", json={"title_zh": "空书", "title_vi": "Sách trống"})
    assert r.status_code == 201
    _, chapters = await _book(r.json()["id"])
    assert chapters == []
    assert (data_dir / "books" / "sach-trong" / "source").is_dir()


async def test_missing_title_zh(api):
    # AC-2.7 (phần API)
    r = await api.post("/api/v1/books", json={"title_zh": "   "})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_duplicate_needs_confirmation(api):
    body = {"title_zh": "大宋有种", "title_vi": "Đại Tống Hữu Chủng"}
    first = await api.post("/api/v1/books", json=body)
    r = await api.post("/api/v1/books", json=body)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "BOOK_DUPLICATE"
    assert r.json()["error"]["details"]["book_id"] == first.json()["id"]
    r = await api.post("/api/v1/books", json={**body, "confirm_duplicate": True})
    assert r.status_code == 201 and r.json()["slug"] == "dai-tong-huu-chung-2"


async def test_same_title_other_author_is_not_duplicate(api):
    await api.post("/api/v1/books", json={"title_zh": "大宋有种", "author": "甲"})
    r = await api.post("/api/v1/books", json={"title_zh": "大宋有种", "author": "乙"})
    assert r.status_code == 201


async def test_import_still_parsing(api):
    view = await upload(api, THREE)
    async with get_sessionmaker()() as s:
        (await s.get(ImportSession, uuid.UUID(view["import_id"]))).status = "parsing"
        await s.commit()
    r = await api.post("/api/v1/books", json={"title_zh": "书", "import_id": view["import_id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "IMPORT_NOT_READY"


async def test_no_chapter_selected(api):
    view = await upload(api, THREE)
    await api.patch(
        f"/api/v1/imports/{view['import_id']}",
        json={"chapters": [{"key": k, "selected": False} for k in ("c0001", "c0002", "c0003")]},
    )
    r = await api.post("/api/v1/books", json={"title_zh": "书", "import_id": view["import_id"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_CHAPTERS_SELECTED"


async def test_invalid_run_config(api):
    r = await api.post("/api/v1/books", json={"title_zh": "书", "run_config": {"beam": 9}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_RUN_CONFIG"


async def test_glossary_source_not_supported_yet(api):
    r = await api.post("/api/v1/books", json={"title_zh": "书", "glossary": {"source": "copy"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "GLOSSARY_SOURCE_UNSUPPORTED"


async def test_ai_extract_creates_only_extract_job(api):
    # AC-2.8: mọi chương ở todo, không có job dịch, chỉ có job ai_extract khi bật AI
    view = await upload(api, THREE)
    r = await api.post(
        "/api/v1/books",
        json={"title_zh": "书", "import_id": view["import_id"],
              "ai_extract": {"enabled": True, "model": "deepseek-v4-pro", "chapters": 20}},
    )
    _, chapters = await _book(r.json()["id"])
    assert {c.status for c in chapters} == {"todo"}
    async with get_sessionmaker()() as s:
        jobs = (await s.scalars(select(Job))).all()
    assert [j.kind for j in jobs] == ["ai_extract"] and len(jobs[0].options["chapter_ids"]) == len(chapters)


async def test_failure_midway_removes_folder_and_keeps_import(api, data_dir, monkeypatch):
    view = await upload(api, THREE)
    real = books_service._write_sources

    def broken(*args):
        real(*args)
        raise OSError("đĩa đầy")

    monkeypatch.setattr(books_service, "_write_sources", broken)
    with pytest.raises(OSError):
        await api.post("/api/v1/books", json={"title_zh": "书", "title_vi": "Sách", "import_id": view["import_id"]})
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(Book)) == 0
    assert not (data_dir / "books" / "sach").exists()
    assert (await api.get(f"/api/v1/imports/{view['import_id']}")).status_code == 200


async def test_existing_folder_is_never_reused_or_deleted(api, data_dir):
    keep = data_dir / "books" / "dai-tong-huu-chung" / "keep.txt"
    keep.parent.mkdir(parents=True)
    keep.write_text("x")
    r = await api.post("/api/v1/books", json={"title_zh": "大宋有种", "title_vi": "Đại Tống Hữu Chủng"})
    assert r.json()["slug"] == "dai-tong-huu-chung-2"
    assert keep.exists()


async def test_model_failure_still_creates_book(clean_db, data_dir):
    # Review Focus 5
    def broken():
        raise FileNotFoundError("chưa tải model")

    app = create_app(translator_factory=broken)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/v1/books", json={"title_zh": "大宋有种"})
    assert r.status_code == 201
    assert r.json()["slug"] == "truyen"
    book, _ = await _book(r.json()["id"])
    assert book.title_vi is None


async def test_run_config_defaults_endpoint(api):
    r = await api.get("/api/v1/run-config/defaults", params={"genre": "modern_war"})
    assert r.status_code == 200
    assert r.json()["honorific"] == {"kinship": False, "pronoun": False, "modern_stable": True}


async def test_double_submit_creates_one_book(api):
    # Bấm "Tạo truyện" hai lần: chỉ một request thắng, request kia thấy phiên đã bị xoá
    view = await upload(api, THREE)
    body = {"title_zh": "大宋有种", "title_vi": "Đại Tống", "import_id": view["import_id"], "confirm_duplicate": True}
    import asyncio

    rs = await asyncio.gather(api.post("/api/v1/books", json=body), api.post("/api/v1/books", json=body))
    assert sorted(r.status_code for r in rs) == [201, 404]
    assert next(r for r in rs if r.status_code == 404).json()["error"]["code"] == "IMPORT_NOT_FOUND"
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(Book)) == 1


async def test_unexpected_error_returns_json_500(clean_db, data_dir, fake_translator):
    app = create_app(translator_factory=lambda: fake_translator, frontend_dist=None)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("hỏng")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/boom")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "INTERNAL_ERROR", "message": "Lỗi không mong muốn ở server", "details": {}}}


async def test_slug_dir_collision_retries_once(api, data_dir, monkeypatch):
    (data_dir / "books" / "dup").mkdir(parents=True)
    picks = iter(["dup", "ok-slug"])

    async def fake_pick(session, title_vi, title_zh):
        return next(picks)

    monkeypatch.setattr(books_service, "_pick_slug", fake_pick)
    r = await api.post("/api/v1/books", json={"title_zh": "大宋有种", "title_vi": "X"})
    assert r.status_code == 201 and r.json()["slug"] == "ok-slug"
    assert (data_dir / "books" / "dup").exists() and not list((data_dir / "books" / "dup").iterdir())
