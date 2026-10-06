import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Chapter, LogEntry, Segment
from app.worker import Worker
from helpers import enqueue, upload

SOURCE = Path(__file__).resolve().parents[3] / "大宋有种--35466"


@pytest.mark.slow
@pytest.mark.db
@pytest.mark.skipif(not SOURCE.is_dir(), reason="thiếu thư mục 大宋有种--35466")
async def test_worker_translates_three_real_chapters(api, data_dir):
    from app.services.translators import default_translator_factory

    paths = sorted(SOURCE.glob("000[1-3] *.txt"))
    view = await upload(api, [(p.name, p.read_bytes()) for p in paths])
    r = await api.post("/api/v1/books", json={"title_zh": "大宋有种", "title_vi": "Đại Tống Hữu Chủng",
                                              "import_id": view["import_id"]})
    book_id = r.json()["id"]
    await enqueue(book_id)
    worker = Worker(default_translator_factory)
    await worker.startup()
    t0 = time.perf_counter()
    while await worker.run_once():
        pass
    elapsed = time.perf_counter() - t0
    async with get_sessionmaker()() as s:
        chapters = (await s.scalars(select(Chapter).where(Chapter.book_id == uuid.UUID(book_id)).order_by(Chapter.no))).all()
        first = (await s.scalars(select(Segment).where(Segment.chapter_id == chapters[0].id).order_by(Segment.idx))).all()
        lines = (await s.scalars(select(LogEntry).where(LogEntry.source == "translate"))).all()
    assert {c.status for c in chapters} <= {"translated", "needs_review"}
    assert all(c.model_id == "HachimiMT-60" for c in chapters)
    assert first[1].dst == first[1].src  # dòng "====" giữ nguyên
    assert len(lines) == 3 and all(l.tokens_in > 0 and l.tokens_out > 0 for l in lines)
    print(f"\n3 chương qua worker: {elapsed:.1f}s · {[(c.no, c.status) for c in chapters]} · {first[0].dst}")
    assert elapsed < 30
