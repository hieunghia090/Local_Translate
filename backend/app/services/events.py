import asyncio
import json
import logging
import uuid

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import AppError
from app.models import Chapter, Job

log = logging.getLogger(__name__)

CHANNEL = "app_events"
MAX_PAYLOAD_BYTES = 7900  # pg_notify giới hạn 8000 byte
QUEUE_SIZE = 1000
WATCHDOG_SECONDS = 5.0
RECONNECT_BACKOFF_START = 1.0
RECONNECT_BACKOFF_MAX = 30.0


def _dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


async def publish(session: AsyncSession, type_: str, book_id, data: dict) -> None:
    """Gửi sự kiện qua NOTIFY. Postgres chỉ phát khi transaction của `session` commit."""
    event = {"type": type_, "book_id": str(book_id) if book_id else None, "data": data}
    payload = _dumps(event)
    if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        payload = _dumps({**event, "data": {"truncated": True}})
    await session.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": payload})


def job_payload(job: Job) -> dict:
    return {
        "id": str(job.id),
        "chapter_id": str(job.chapter_id) if job.chapter_id else None,
        "kind": job.kind,
        "engine": job.engine,
        "status": job.status,
        "progress": job.progress,
        "position": job.position,
        "tokens_in": job.tokens_in,
        "tokens_out": job.tokens_out,
        "error": job.error,
    }


def chapter_payload(ch: Chapter) -> dict:
    return {
        "id": str(ch.id),
        "no": ch.no,
        "status": ch.status,
        "model_id": ch.model_id,
        "title_vi": ch.title_vi,
        "error": ch.error,
        "translated_at": ch.translated_at,
    }


async def emit_job(session: AsyncSession, job: Job) -> None:
    await publish(session, "job.updated", job.book_id, job_payload(job))


async def emit_chapter(session: AsyncSession, ch: Chapter) -> None:
    await publish(session, "chapter.updated", ch.book_id, chapter_payload(ch))


async def emit_book_stats(session: AsyncSession, book_id: uuid.UUID) -> None:
    from app.services.library import book_stats  # tránh import vòng

    await session.flush()
    stats = await book_stats(session, book_id)
    if stats is not None:
        await publish(session, "book.stats_updated", book_id, {"book_id": str(book_id), **stats})


def format_sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {_dumps(event)}\n\n"


class EventBroker:
    """Một kết nối LISTEN cho cả server, phát sự kiện tới các hàng đợi của client SSE."""

    def __init__(self) -> None:
        self._conn: asyncpg.Connection | None = None
        self._subs: dict[asyncio.Queue, str | None] = {}
        self._lock = asyncio.Lock()
        self._watchdog: asyncio.Task | None = None
        self._wake = asyncio.Event()

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def _alive(self) -> bool:
        return self._conn is not None and not self._conn.is_closed()

    async def _ensure_connection(self) -> bool:
        """Trả True nếu vừa (kết nối lại) tạo kết nối mới."""
        async with self._lock:
            if self._alive():
                return False
            if self._conn is not None:
                self._conn.terminate()
                self._conn = None
            conn = await asyncpg.connect(get_settings().database_url)
            try:
                await conn.add_listener(CHANNEL, self._on_notify)
                conn.add_termination_listener(self._on_terminated)
            except BaseException:
                conn.terminate()
                raise
            self._conn = conn
            return True

    def _on_terminated(self, _conn) -> None:
        self._wake.set()  # đánh thức watchdog để kết nối lại ngay

    async def _watch(self) -> None:
        """Nếu kết nối LISTEN đứt thì kết nối lại (backoff 1s → 30s), rồi báo client tải lại dữ liệu."""
        backoff = RECONNECT_BACKOFF_START
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), WATCHDOG_SECONDS)
            except TimeoutError:
                pass
            self._wake.clear()
            while self._subs and not self._alive():
                try:
                    await self._ensure_connection()
                except Exception:
                    log.warning("Kết nối lại LISTEN thất bại, thử lại sau %.1fs", backoff, exc_info=True)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)
                    continue
                backoff = RECONNECT_BACKOFF_START
                self._broadcast({"type": "resync", "book_id": None, "data": {}})

    def _broadcast(self, event: dict) -> None:
        for queue in list(self._subs):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("Hàng đợi SSE đầy, bỏ sự kiện %s", event["type"])

    def _on_notify(self, _conn, _pid, _channel, payload: str) -> None:
        try:
            event = json.loads(payload)
        except ValueError:
            log.warning("Bỏ qua NOTIFY không phải JSON: %.80s", payload)
            return
        if not isinstance(event, dict) or "type" not in event:
            return
        target = event.get("book_id")
        for queue, book_id in list(self._subs.items()):
            if book_id is None or target is None or target == book_id:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    log.warning("Hàng đợi SSE đầy, bỏ sự kiện %s", event["type"])

    async def subscribe(self, book_id: str | None) -> asyncio.Queue:
        try:
            await self._ensure_connection()
        except Exception as e:
            log.warning("Không kết nối được LISTEN: %s", e)
            raise AppError("EVENTS_UNAVAILABLE", "Không kết nối được tới cơ sở dữ liệu để nhận sự kiện", 503) from e
        if self._watchdog is None or self._watchdog.done():
            self._watchdog = asyncio.create_task(self._watch())
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subs[queue] = book_id
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subs.pop(queue, None)

    async def close(self) -> None:
        self._subs.clear()
        if self._watchdog is not None:
            self._watchdog.cancel()
            await asyncio.gather(self._watchdog, return_exceptions=True)
            self._watchdog = None
        if self._conn is not None and not self._conn.is_closed():
            await self._conn.close()
        self._conn = None
