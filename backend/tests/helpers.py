import uuid
from datetime import datetime

from app.core.run_config import default_run_config
from app.db import get_sessionmaker
from app.ids import uuid7
from app.models import Book, Chapter


async def seed_book(
    *,
    title_zh: str = "大宋有种",
    title_vi: str | None = None,
    author: str | None = None,
    genre: str = "other",
    statuses: tuple[str, ...] | list[str] = (),
    last_opened_at: datetime | None = None,
) -> uuid.UUID:
    async with get_sessionmaker()() as s:
        book = Book(
            slug=f"b-{uuid7().hex[-12:]}",
            title_zh=title_zh,
            title_vi=title_vi,
            author=author,
            genre=genre,
            run_config=default_run_config(genre),
            last_opened_at=last_opened_at,
        )
        s.add(book)
        await s.flush()
        s.add_all(
            Chapter(book_id=book.id, no=i, title_zh=f"第{i}章", status=st, char_count=1000, source_hash="x")
            for i, st in enumerate(statuses, start=1)
        )
        await s.commit()
        return book.id

import asyncio
import time


async def wait_ready(api, import_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = (await api.get(f"/api/v1/imports/{import_id}")).json()
        if body.get("status") != "parsing" or time.monotonic() > deadline:
            return body
        await asyncio.sleep(0.05)


async def upload(api, files: list[tuple[str, bytes]], mode: str = "multi", **fields) -> dict:
    r = await api.post(
        "/api/v1/imports",
        data={"mode": mode, **fields},
        files=[("files[]", (name, content, "text/plain")) for name, content in files],
    )
    assert r.status_code == 202, r.text
    return await wait_ready(api, r.json()["import_id"])


def chapter_text(no: int, lines: int) -> bytes:
    body = "\n".join(f"第{no}章第{i}句话。" for i in range(1, lines + 1))
    return f"第{no}章 标题{no}\n=========\n\n{body}\n".encode()


async def make_book(api, n_chapters: int = 3, lines: int = 4, title: str = "书"):
    """Tạo truyện thật qua API (có file source) và trả (book_id, danh sách Chapter theo no)."""
    from sqlalchemy import select

    from app.models import Chapter

    files = [(f"{no:04d}.txt", chapter_text(no, lines)) for no in range(1, n_chapters + 1)]
    view = await upload(api, files)
    # Chương mẫu ngắn nên import mặc định bỏ chọn (cờ SHORT): chọn lại tất cả.
    sel = await api.patch(
        f"/api/v1/imports/{view['import_id']}",
        json={"chapters": [{"key": c["key"], "selected": True} for c in view["chapters"]]},
    )
    assert sel.status_code == 200, sel.text
    r = await api.post(
        "/api/v1/books", json={"title_zh": title, "title_vi": title, "import_id": view["import_id"], "confirm_duplicate": True}
    )
    assert r.status_code == 201, r.text
    book_id = r.json()["id"]
    async with get_sessionmaker()() as s:
        chapters = (
            await s.scalars(select(Chapter).where(Chapter.book_id == uuid.UUID(book_id)).order_by(Chapter.no))
        ).all()
    return book_id, list(chapters)


async def enqueue(book_id, chapter_ids=None, **kw) -> list[uuid.UUID]:
    from sqlalchemy import select

    from app.models import Book, Chapter
    from app.services import queue

    async with get_sessionmaker()() as s:
        book = await s.get(Book, uuid.UUID(str(book_id)))
        stmt = select(Chapter).where(Chapter.book_id == book.id)
        if chapter_ids is not None:
            stmt = stmt.where(Chapter.id.in_(chapter_ids))
        jobs = await queue.enqueue_chapters(s, book, list((await s.scalars(stmt)).all()), **kw)
        await s.commit()
        return [j.id for j in jobs]
