import asyncio
import json

import pytest
from sqlalchemy import text

from app.db import get_sessionmaker
from app.services import events
from app.services.library import book_stats
from helpers import seed_book

pytestmark = pytest.mark.db


async def _next(q: asyncio.Queue, timeout: float = 2.0) -> dict:
    return await asyncio.wait_for(q.get(), timeout)


async def test_publish_reaches_subscriber_after_commit(clean_db):
    broker = events.EventBroker()
    q_all = await broker.subscribe(None)
    q_book = await broker.subscribe("11111111-1111-7111-8111-111111111111")
    q_other = await broker.subscribe("22222222-2222-7222-8222-222222222222")
    try:
        async with get_sessionmaker()() as s:
            await events.publish(s, "job.updated", "11111111-1111-7111-8111-111111111111", {"progress": 40})
            await asyncio.sleep(0.2)
            assert q_all.empty()  # chưa commit thì chưa gửi
            await s.commit()
        got = await _next(q_all)
        assert got == {"type": "job.updated", "book_id": "11111111-1111-7111-8111-111111111111", "data": {"progress": 40}}
        assert (await _next(q_book))["data"] == {"progress": 40}
        await asyncio.sleep(0.2)
        assert q_other.empty()
    finally:
        await broker.close()


async def test_system_events_reach_every_subscriber(clean_db):
    broker = events.EventBroker()
    q_book = await broker.subscribe("11111111-1111-7111-8111-111111111111")
    try:
        async with get_sessionmaker()() as s:
            await events.publish(s, "log.appended", None, {"message": "Nạp model"})
            await s.commit()
        assert (await _next(q_book))["type"] == "log.appended"
    finally:
        await broker.close()


async def test_bad_or_oversized_payload_does_not_break_broker(clean_db):
    # Review Focus 5
    broker = events.EventBroker()
    q = await broker.subscribe(None)
    try:
        async with get_sessionmaker()() as s:
            await s.execute(text("SELECT pg_notify('app_events', 'không phải json')"))
            await events.publish(s, "chapter.updated", None, {"blob": "字" * 10_000})
            await events.publish(s, "job.updated", None, {"ok": True})
            await s.commit()
        first = await _next(q)
        assert first["type"] == "chapter.updated" and first["data"] == {"truncated": True}
        assert (await _next(q))["data"] == {"ok": True}
    finally:
        await broker.close()


async def test_unsubscribe_removes_queue(clean_db):
    broker = events.EventBroker()
    q = await broker.subscribe(None)
    assert broker.subscriber_count == 1
    broker.unsubscribe(q)
    assert broker.subscriber_count == 0
    await broker.close()


def test_format_sse():
    out = events.format_sse({"type": "job.updated", "book_id": None, "data": {"x": "Chương"}})
    assert out.startswith("event: job.updated\ndata: ")
    assert out.endswith("\n\n")
    assert json.loads(out.split("data: ", 1)[1])["data"] == {"x": "Chương"}


async def test_book_stats(clean_db):
    book_id = await seed_book(statuses=["translated", "todo", "reviewed", "error"])
    async with get_sessionmaker()() as s:
        got = await book_stats(s, book_id)
    assert got["stats"]["total"] == 4 and got["stats"]["error"] == 1
    assert (got["progress_pct"], got["state"]) == (50, "in_progress")


async def test_broker_reconnects_after_listen_connection_dropped(clean_db, monkeypatch):
    # SSE không được im lặng khi kết nối LISTEN bị đứt: kết nối lại, gửi resync, rồi nhận sự kiện tiếp
    monkeypatch.setattr(events, "WATCHDOG_SECONDS", 0.1)
    monkeypatch.setattr(events, "RECONNECT_BACKOFF_START", 0.05)
    broker = events.EventBroker()
    q = await broker.subscribe(None)
    try:
        pid = broker._conn.get_server_pid()
        async with get_sessionmaker()() as s:
            await s.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            await s.commit()
        got = await _next(q, 10.0)
        assert got == {"type": "resync", "book_id": None, "data": {}}
        assert broker._conn is not None and not broker._conn.is_closed()
        async with get_sessionmaker()() as s:
            await events.publish(s, "job.updated", None, {"ok": True})
            await s.commit()
        assert (await _next(q))["data"] == {"ok": True}
    finally:
        await broker.close()
    assert broker._watchdog is None


async def test_subscribe_raises_app_error_when_db_unreachable(clean_db, monkeypatch):
    import asyncpg
    import pytest as _pytest

    from app.errors import AppError

    async def boom(*a, **k):
        raise OSError("không kết nối được")

    monkeypatch.setattr(asyncpg, "connect", boom)
    broker = events.EventBroker()
    with _pytest.raises(AppError) as exc:
        await broker.subscribe(None)
    assert exc.value.code == "EVENTS_UNAVAILABLE" and exc.value.status == 503
    assert broker.subscriber_count == 0
    await broker.close()
