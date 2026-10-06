import asyncio
import re
import time
import uuid
import zipfile
from datetime import datetime

import pytest
from ebooklib import epub

from app.core.run_config import default_run_config
from app.db import get_sessionmaker
from app.ids import uuid7
from app.models import DONE_STATUSES, Book, Chapter, Export, Segment
from app.services import exports
from app.services.paths import books_root

pytestmark = pytest.mark.db


async def seed_translated(statuses: dict[int, str], *, title_vi: str = "Đại Tống", author: str | None = "Tác giả") -> uuid.UUID:
    """Truyện có sẵn segment trong DB. Chèn theo thứ tự của dict, cố ý khác thứ tự `no`."""
    async with get_sessionmaker()() as s:
        book = Book(slug=f"b-{uuid7().hex[-12:]}", title_zh="大宋", title_vi=title_vi, author=author, genre="other",
                    run_config=default_run_config("other"))
        s.add(book)
        await s.flush()
        for no, status in statuses.items():
            ch = Chapter(book_id=book.id, no=no, title_zh=f"第{no}章", title_vi=f"Chương {no}", status=status,
                         char_count=10, source_hash="x")
            s.add(ch)
            await s.flush()
            done = status in DONE_STATUSES
            s.add_all([
                Segment(chapter_id=ch.id, idx=0, src=f"第{no}章", is_meta=False, dst=f"Chương {no} máy" if done else None),
                Segment(chapter_id=ch.id, idx=1, src="Nguồn: https://x.vn/1", is_meta=True, dst="Nguồn: https://x.vn/1"),
                Segment(chapter_id=ch.id, idx=2, src=f"正文{no}。", is_meta=False, dst=f"Nội dung {no}." if done else None),
            ])
        await s.commit()
        (books_root() / book.slug).mkdir(parents=True, exist_ok=True)  # import tạo thư mục truyện
        return book.id


async def wait_export(api, export_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        view = (await api.get(f"/api/v1/exports/{export_id}")).json()
        if view["status"] != "running" or time.monotonic() > deadline:
            return view
        await asyncio.sleep(0.05)


async def export(api, book_id, **body) -> dict:
    r = await api.post(f"/api/v1/books/{book_id}/exports", json=body)
    assert r.status_code == 202, r.text
    return await wait_export(api, r.json()["export_id"])


async def slug_of(api, book_id) -> str:
    return (await api.get(f"/api/v1/books/{book_id}")).json()["slug"]


async def test_ac_3_8_merged_txt_reviewed_only_in_no_order(api, data_dir):
    # AC-3.8, BR-3.15, BR-3.16
    book_id = await seed_translated({7: "reviewed", 2: "reviewed", 5: "translated", 9: "reviewed",
                                     1: "todo", 4: "reviewed", 3: "needs_review"})
    p = (await api.get(f"/api/v1/books/{book_id}/exports/preview", params={"scope": "reviewed"})).json()
    assert p == {"chapters": 4, "skipped": 3}
    view = await export(api, book_id, scope="reviewed", format="txt")
    assert view["status"] == "done" and (view["chapters"], view["skipped"]) == (4, 3)
    slug = await slug_of(api, book_id)
    assert re.fullmatch(rf"{slug}_da-soat_\d{{8}}-\d{{4}}\.txt", view["file_name"])
    folder = data_dir / "books" / slug / "exports"
    assert [f.name for f in folder.iterdir()] == [view["file_name"]]

    d = await api.get(view["download_url"])
    assert d.status_code == 200
    assert d.headers["content-type"].startswith("text/plain") and "charset=utf-8" in d.headers["content-type"]
    assert f'filename="{view["file_name"]}"' in d.headers["content-disposition"]
    text = d.content.decode("utf-8")
    assert [line for line in text.splitlines() if line.startswith("Chương")] == ["Chương 2", "Chương 4", "Chương 7", "Chương 9"]
    assert "Nội dung 5." not in text and "Nguồn:" not in text and "máy" not in text


async def test_range_zip_skips_untranslated(api, tmp_path):
    book_id = await seed_translated({1: "translated", 2: "todo", 3: "needs_review", 4: "reviewed"})
    p = (await api.get(f"/api/v1/books/{book_id}/exports/preview",
                       params={"scope": "range", "from_no": 2, "to_no": 3})).json()
    assert p == {"chapters": 1, "skipped": 3}
    view = await export(api, book_id, scope="range", from_no=2, to_no=3, format="zip")
    assert "_chuong-2-3_" in view["file_name"] and view["file_name"].endswith(".zip")
    d = await api.get(view["download_url"])
    assert d.headers["content-type"] == "application/zip"
    path = tmp_path / "x.zip"
    path.write_bytes(d.content)
    with zipfile.ZipFile(path) as zf:
        assert zf.namelist() == ["0003.txt"]
        assert zf.read("0003.txt").decode() == "Chương 3\n\nNội dung 3.\n"


async def test_epub_toc_metadata_and_bilingual(api, tmp_path):
    # BR-3.17
    book_id = await seed_translated({3: "reviewed", 1: "translated", 2: "todo"})
    view = await export(api, book_id, scope="translated", format="epub")
    d = await api.get(view["download_url"])
    assert d.headers["content-type"] == "application/epub+zip"
    path = tmp_path / "book.epub"
    path.write_bytes(d.content)
    book = epub.read_epub(str(path))
    assert [link.title for link in book.toc] == ["Chương 1", "Chương 3"]
    assert book.get_metadata("DC", "language")[0][0] == "vi"
    assert book.get_metadata("DC", "title")[0][0] == "Đại Tống"
    assert [m[0] for m in book.get_metadata("DC", "creator")] == ["Tác giả"]

    view = await export(api, book_id, scope="translated", format="bilingual")
    assert "_da-dich-song-ngu_" in view["file_name"] and view["file_name"].endswith(".txt")
    text = (await api.get(view["download_url"])).content.decode("utf-8")
    assert "正文1。\nNội dung 1." in text and "正文3。\nNội dung 3." in text and "正文2。" not in text


async def test_keep_meta_and_no_titles(api):
    book_id = await seed_translated({1: "translated"})
    view = await export(api, book_id, format="txt", keep_meta=True, include_titles=False)
    text = (await api.get(view["download_url"])).content.decode("utf-8")
    assert text == "Chương 1 máy\nNguồn: https://x.vn/1\nNội dung 1.\n"


async def test_validation_and_errors(api):
    book_id = await seed_translated({1: "todo"})
    r = await api.post(f"/api/v1/books/{book_id}/exports", json={"scope": "range", "format": "txt"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    r = await api.get(f"/api/v1/books/{book_id}/exports/preview", params={"scope": "range", "from_no": 5, "to_no": 2})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_RANGE"
    r = await api.post(f"/api/v1/books/{book_id}/exports", json={"scope": "translated", "format": "txt"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOTHING_TO_EXPORT"
    r = await api.get(f"/api/v1/exports/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "EXPORT_NOT_FOUND"
    r = await api.get(f"/api/v1/books/{uuid.uuid4()}/exports/preview")
    assert r.status_code == 404 and r.json()["error"]["code"] == "BOOK_NOT_FOUND"


async def test_download_missing_file(api, data_dir):
    book_id = await seed_translated({1: "translated"})
    view = await export(api, book_id, format="txt")
    (data_dir / "books" / await slug_of(api, book_id) / "exports" / view["file_name"]).unlink()
    r = await api.get(view["download_url"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "EXPORT_FILE_MISSING"


async def test_failed_export_reports_error_and_leaves_no_partial_file(api, data_dir, monkeypatch):
    # Review Focus 3
    def boom(path, fmt, chapters, opts, **kw):
        path.write_bytes(b"half")
        raise OSError("Hết dung lượng đĩa")

    monkeypatch.setattr(exports, "write_export", boom)
    book_id = await seed_translated({1: "translated"})
    view = await export(api, book_id, format="txt")
    assert view["status"] == "failed" and "Hết dung lượng đĩa" in view["error"] and view["download_url"] is None
    folder = data_dir / "books" / await slug_of(api, book_id) / "exports"
    assert list(folder.iterdir()) == []
    r = await api.get(f"/api/v1/exports/{view['id']}/download")
    assert r.status_code == 409 and r.json()["error"]["code"] == "EXPORT_NOT_READY"


async def test_stale_running_export_marked_failed_and_part_removed(api, data_dir):
    # Review Focus 3: server tắt giữa lúc xuất
    book_id = await seed_translated({1: "translated"})
    async with get_sessionmaker()() as s:
        exp = Export(book_id=book_id, scope="translated", format="txt", include_titles=True, keep_meta=False,
                     status="running")
        s.add(exp)
        await s.commit()
        export_id = exp.id
    part = data_dir / "books" / "x" / "exports" / "x_da-dich_20261004-0905.txt.part"
    part.parent.mkdir(parents=True)
    part.write_text("dở")
    async with get_sessionmaker()() as s:
        assert await exports.fail_stale_exports(s) == 1
        await s.commit()
    assert exports.remove_partial_files() == 1 and not part.exists()
    view = (await api.get(f"/api/v1/exports/{export_id}")).json()
    assert view["status"] == "failed" and "Server tắt" in view["error"] and view["download_url"] is None


async def test_same_minute_exports_do_not_overwrite(api, data_dir, monkeypatch):
    # Review Focus 4
    monkeypatch.setattr(exports, "_now", lambda: datetime(2026, 10, 4, 9, 5).astimezone())
    book_id = await seed_translated({1: "translated"})
    first = await export(api, book_id, format="txt")
    second = await export(api, book_id, format="txt")
    slug = await slug_of(api, book_id)
    assert first["file_name"] == f"{slug}_da-dich_20261004-0905.txt"
    assert second["file_name"] == f"{slug}_da-dich_20261004-0905-2.txt"
    folder = data_dir / "books" / slug / "exports"
    assert sorted(f.name for f in folder.iterdir()) == sorted([first["file_name"], second["file_name"]])


async def test_export_after_book_deleted_does_not_recreate_folder(api, data_dir, monkeypatch):
    # Xoá truyện lúc đang ghi file: file và thư mục không được sót lại
    book_id = await seed_translated({1: "translated"})
    slug = await slug_of(api, book_id)
    real = exports.write_export

    def write_then_delete(path, *a, **kw):
        real(path, *a, **kw)
        asyncio.run(_delete_row(book_id))  # DB mất dòng, file đã ghi xong

    async def _delete_row(bid):
        from sqlalchemy import delete
        async with get_sessionmaker()() as s:
            await s.execute(delete(Book).where(Book.id == bid))
            await s.commit()

    monkeypatch.setattr(exports, "write_export", write_then_delete)
    r = await api.post(f"/api/v1/books/{book_id}/exports", json={"format": "txt"})
    assert r.status_code == 202
    await asyncio.sleep(0.5)
    assert not (books_root() / slug).exists()


async def test_export_fails_when_book_folder_missing(api, data_dir):
    book_id = await seed_translated({1: "translated"})
    slug = await slug_of(api, book_id)
    import shutil
    shutil.rmtree(books_root() / slug)
    view = await export(api, book_id, format="txt")
    assert view["status"] == "failed"
    assert not (books_root() / slug).exists()
