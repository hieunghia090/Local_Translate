import asyncio
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.events import format_sse

router = APIRouter()
HEARTBEAT_SECONDS = 15


@router.get("/events")
async def stream_events(request: Request, book_id: uuid.UUID | None = None):
    broker = request.app.state.broker
    queue = await broker.subscribe(str(book_id) if book_id else None)

    async def body():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                except TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
                    continue
                yield format_sse(event)
        finally:
            broker.unsubscribe(queue)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
