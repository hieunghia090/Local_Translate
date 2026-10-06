import asyncio
import json
import time
import uuid

import httpx
import pytest
import uvicorn

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.main import create_app
from app.services import logs
from app.worker import Worker
from helpers import enqueue, make_book

pytestmark = pytest.mark.db


class LiveServer:
    def __init__(self, app):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"))
        self.task: asyncio.Task | None = None

    async def __aenter__(self) -> str:
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.02)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def __aexit__(self, *exc):
        self.server.should_exit = True
        await self.task


async def read_events(response: httpx.Response, until_type: str, timeout: float = 5.0) -> list[dict]:
    got: list[dict] = []

    # aiter_lines() chỉ được gọi một lần trên mỗi response: giữ iterator để gọi read_events nhiều lần.
    if not hasattr(response, "_lines"):
        response._lines = response.aiter_lines()

    async def consume():
        async for line in response._lines:
            if line.startswith("data: "):
                event = json.loads(line[6:])
                got.append(event)
                if event["type"] == until_type:
                    return

    await asyncio.wait_for(consume(), timeout)
    return got


async def test_log_appended_arrives_within_one_second(clean_db, data_dir, fake_translator):
    # AC-5.5
    app = create_app(translator_factory=lambda: fake_translator)
    async with LiveServer(app) as base, httpx.AsyncClient(base_url=base, timeout=10) as client:
        async with client.stream("GET", "/api/v1/events") as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            await asyncio.sleep(0.2)  # chờ kết nối LISTEN
            t0 = time.perf_counter()
            async with get_sessionmaker()() as s:
                await logs.write_log(s, level="info", source="system", message="Xin chào SSE")
                await s.commit()
            events = await read_events(response, "log.appended")
            assert time.perf_counter() - t0 < 1.0
    assert events[-1]["data"]["message"] == "Xin chào SSE"


async def test_worker_progress_reaches_browser(clean_db, data_dir, fake_translator):
    # AC-3.4: dòng chương đổi trạng thái mà không cần tải lại
    app = create_app(translator_factory=lambda: fake_translator)
    async with LiveServer(app) as base, httpx.AsyncClient(base_url=base, timeout=10) as client:
        book_id, chapters = await make_book(client, n_chapters=1, lines=2)
        await enqueue(book_id)
        async with client.stream("GET", "/api/v1/events", params={"book_id": book_id}) as response:
            await asyncio.sleep(0.2)
            await Worker(lambda: fake_translator).run_once()
            events = await read_events(response, "book.stats_updated")
            while not any(e["type"] == "chapter.updated" and e["data"]["status"] == "translated" for e in events):
                events += await read_events(response, "book.stats_updated")
    types = {e["type"] for e in events}
    assert {"job.updated", "chapter.updated", "book.stats_updated", "log.appended"} <= types
    done = [e for e in events if e["type"] == "chapter.updated" and e["data"]["status"] == "translated"][-1]
    assert done["data"]["model_id"] == "fake" and done["data"]["no"] == 1


async def test_closing_stream_unsubscribes(clean_db, data_dir, fake_translator):
    # Review Focus 5
    app = create_app(translator_factory=lambda: fake_translator)
    async with LiveServer(app) as base, httpx.AsyncClient(base_url=base, timeout=10) as client:
        async with client.stream("GET", "/api/v1/events") as response:
            await response.aiter_lines().__anext__()  # ": connected"
            assert app.state.broker.subscriber_count == 1
        for _ in range(50):
            if app.state.broker.subscriber_count == 0:
                break
            await asyncio.sleep(0.05)
        assert app.state.broker.subscriber_count == 0


async def test_events_for_other_books_filtered(clean_db, data_dir, fake_translator):
    app = create_app(translator_factory=lambda: fake_translator)
    mine, other = str(uuid.uuid4()), str(uuid.uuid4())
    async with LiveServer(app) as base, httpx.AsyncClient(base_url=base, timeout=10) as client:
        async with client.stream("GET", "/api/v1/events", params={"book_id": mine}) as response:
            await asyncio.sleep(0.2)
            async with get_sessionmaker()() as s:
                await logs.write_log(s, level="info", source="system", message="khác", book_id=uuid.UUID(other))
                await logs.write_log(s, level="info", source="system", message="của tôi", book_id=uuid.UUID(mine))
                await s.commit()
            events = await read_events(response, "log.appended")
    assert [e["data"]["message"] for e in events] == ["của tôi"]
