import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.db import get_sessionmaker
from app.services import logs
from helpers import seed_book

pytestmark = pytest.mark.db


async def _log(book_id, **kw):
    async with get_sessionmaker()() as s:
        entry = await logs.write_log(s, book_id=book_id, **kw)
        await s.commit()
        return entry


async def seed_logs(book_id):
    await _log(book_id, level="info", source="translate", message="Chương 1 · 10 câu", chapter_no=1,
               provider="ct2", model="HachimiMT-60", tokens_in=100, tokens_out=120, latency_ms=900)
    await _log(book_id, level="error", source="translate", message="Chương 2 · lỗi encoding", chapter_no=2,
               detail={"error": {"type": "SourceDecodeError", "message": "x", "stack": "..."}})
    await _log(book_id, level="error", source="glossary", message="Gọi AI lỗi 429")
    await _log(book_id, level="warn", source="system", message="Giảm batch 8 → 4")


async def test_list_newest_first_with_filters(api):
    book_id = await seed_book()
    await seed_logs(book_id)
    r = await api.get(f"/api/v1/books/{book_id}/logs")
    items = r.json()["items"]
    assert [i["message"] for i in items] == ["Giảm batch 8 → 4", "Gọi AI lỗi 429", "Chương 2 · lỗi encoding", "Chương 1 · 10 câu"]
    assert r.json()["next_cursor"] is None
    # AC-5.4
    r = await api.get(f"/api/v1/books/{book_id}/logs", params={"level": "error", "source": "translate"})
    assert [i["message"] for i in r.json()["items"]] == ["Chương 2 · lỗi encoding"]
    r = await api.get(f"/api/v1/books/{book_id}/logs", params={"q": "batch"})
    assert [i["level"] for i in r.json()["items"]] == ["warn"]
    r = await api.get(f"/api/v1/books/{book_id}/logs", params={"chapter_no": 1})
    assert r.json()["items"][0]["tokens_in"] == 100


async def test_pagination_with_before_cursor(api):
    book_id = await seed_book()
    await seed_logs(book_id)
    first = (await api.get(f"/api/v1/books/{book_id}/logs", params={"limit": 3})).json()
    assert len(first["items"]) == 3 and first["next_cursor"]
    rest = (await api.get(f"/api/v1/books/{book_id}/logs", params={"limit": 3, "before": first["next_cursor"]})).json()
    assert [i["message"] for i in rest["items"]] == ["Chương 1 · 10 câu"]


async def test_logs_of_other_books_hidden(api):
    a, b = await seed_book(), await seed_book(title_zh="仙逆")
    await _log(a, level="info", source="system", message="A")
    await _log(b, level="info", source="system", message="B")
    assert [i["message"] for i in (await api.get(f"/api/v1/books/{a}/logs")).json()["items"]] == ["A"]


async def test_export_matches_filter(api):
    # AC-5.4
    book_id = await seed_book()
    await seed_logs(book_id)
    r = await api.get(f"/api/v1/books/{book_id}/logs/export", params={"level": "error"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    rows = [json.loads(line) for line in r.text.strip().split("\n")]
    assert [row["message"] for row in rows] == ["Chương 2 · lỗi encoding", "Gọi AI lỗi 429"]
    assert rows[0]["detail"]["error"]["type"] == "SourceDecodeError"


async def test_summary_and_detail(api):
    book_id = await seed_book()
    await seed_logs(book_id)
    assert (await api.get(f"/api/v1/books/{book_id}/logs/summary")).json() == {"total": 4, "errors": 2}
    one = (await api.get(f"/api/v1/books/{book_id}/logs", params={"chapter_no": 2})).json()["items"][0]
    full = (await api.get(f"/api/v1/logs/{one['id']}")).json()
    assert full["detail"]["error"]["stack"] == "..."
    assert (await api.get(f"/api/v1/logs/{uuid.uuid4()}")).json()["error"]["code"] == "LOG_NOT_FOUND"


async def test_clear_keeps_one_info_line(api):
    # BR-5.6
    book_id = await seed_book()
    await seed_logs(book_id)
    assert (await api.delete(f"/api/v1/books/{book_id}/logs")).status_code == 204
    items = (await api.get(f"/api/v1/books/{book_id}/logs")).json()["items"]
    assert len(items) == 1 and items[0]["level"] == "info" and items[0]["message"].startswith("Log đã được xoá lúc")


async def test_unknown_book(api):
    r = await api.get(f"/api/v1/books/{uuid.uuid4()}/logs")
    assert r.status_code == 404 and r.json()["error"]["code"] == "BOOK_NOT_FOUND"


async def test_key_never_stored(api, monkeypatch):
    # AC-5.3 (phần ghi log)
    from app.config import get_settings

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-live-abcdef123456")
    get_settings.cache_clear()
    try:
        book_id = await seed_book()
        await _log(book_id, level="error", source="system", message="DeepSeek 401",
                   detail={"request": {"headers": {"Authorization": "Bearer sk-live-abcdef123456"}}})
        async with get_sessionmaker()() as s:
            dump = (await s.execute(text("SELECT string_agg(detail::text || message, '') FROM log_entries"))).scalar()
        assert "sk-live-abcdef123456" not in dump
    finally:
        get_settings.cache_clear()


async def test_purge_drops_old_partitions_and_rows(clean_db):
    # Review Focus 4 + giữ 30 ngày
    old = datetime.now(timezone.utc) - timedelta(days=31)
    month = old.date().replace(day=1)
    next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    async with get_sessionmaker()() as s:
        for name, start, end in (("log_entries_2001_01", "2001-01-01", "2001-02-01"),
                                 (f"log_entries_{month:%Y_%m}", str(month), str(next_month))):
            await s.execute(text(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF log_entries FOR VALUES FROM ('{start}') TO ('{end}')"
            ))
        insert = text("INSERT INTO log_entries (id, ts, level, source, message) VALUES (gen_random_uuid(), :ts, 'info', 'system', :m)")
        await s.execute(insert, {"ts": datetime(2001, 1, 15, tzinfo=timezone.utc), "m": "cũ"})
        await s.execute(insert, {"ts": old, "m": "quá 30 ngày"})
        await logs.write_log(s, level="info", source="system", message="mới")
        await s.commit()
        dropped = await logs.purge_old_logs(s)
        await s.commit()
        messages = set((await s.execute(text("SELECT message FROM log_entries"))).scalars())
    assert "log_entries_2001_01" in dropped
    assert messages == {"mới"}


async def test_ensure_partitions_is_idempotent(clean_db):
    async with get_sessionmaker()() as s:
        await logs.ensure_partitions(s)
        await logs.ensure_partitions(s)
        await s.commit()
