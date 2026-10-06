import os
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker
from app.deepseek.catalog import DEEPSEEK_MODELS
from app.deepseek.client import DeepSeekClient
from app.deepseek.guards import han_stats
from app.deepseek.pool import DeepSeekPool
from app.models import Chapter, LogEntry, Segment
from helpers import enqueue, upload

SOURCE = Path(__file__).resolve().parents[3] / "大宋有种--35466"


# Không có marker `db`: `-m db` không bao giờ gom test này. Chỉ chạy khi `-m live` VÀ LT_LIVE=1.
@pytest.mark.live
@pytest.mark.skipif(os.environ.get("LT_LIVE") != "1", reason="gọi API thật: chỉ chạy qua `make test-live` (LT_LIVE=1)")
@pytest.mark.skipif(not SOURCE.is_dir(), reason="thiếu thư mục 大宋有种--35466")
async def test_deepseek_translates_one_real_chapter(api, data_dir):
    from app.services.translators import default_translator_factory

    settings = get_settings()
    if not settings.deepseek_api_key.strip():
        pytest.skip("Chưa có DEEPSEEK_API_KEY trong .env")
    model = settings.deepseek_default_model if settings.deepseek_default_model in DEEPSEEK_MODELS else "deepseek-v4-pro"
    path = (sorted(SOURCE.glob("0016 *.txt")) or sorted(SOURCE.glob("*.txt")))[0]
    view = await upload(api, [(path.name, path.read_bytes())])
    await api.patch(f"/api/v1/imports/{view['import_id']}",
                    json={"chapters": [{"key": c["key"], "selected": True} for c in view["chapters"]]})
    r = await api.post("/api/v1/books", json={
        "title_zh": "大宋有种", "title_vi": "Đại Tống Hữu Chủng", "genre": "xianxia", "import_id": view["import_id"],
        "confirm_duplicate": True, "run_config": {"engine": "deepseek", "model_id": model}})
    assert r.status_code == 201, r.text
    book_id = r.json()["id"]
    await enqueue(book_id)

    t0 = time.perf_counter()
    # Pool dùng key thật trong .env; fixture `api` vẫn dùng client giả cho các route.
    await DeepSeekPool(DeepSeekClient.from_settings, translator_factory=default_translator_factory).run_until_idle()
    elapsed = time.perf_counter() - t0

    async with get_sessionmaker()() as s:
        ch = (await s.scalars(select(Chapter).where(Chapter.book_id == uuid.UUID(book_id)))).one()
        segs = (await s.scalars(select(Segment).where(Segment.chapter_id == ch.id).order_by(Segment.idx))).all()
        line = (await s.scalars(select(LogEntry).where(LogEntry.source == "translate", LogEntry.book_id == ch.book_id))).one()
    assert ch.status in ("translated", "needs_review"), ch.error
    assert ch.model_id == model
    for seg in segs:
        if seg.is_meta:
            assert seg.dst == seg.src  # AC-8.1: dòng === và Nguồn: giữ nguyên
        else:
            assert seg.dst and seg.dst.strip()
    han, letters = han_stats("\n".join(seg.dst for seg in segs if not seg.is_meta))
    assert han / max(letters, 1) < 0.3  # G1
    assert line.tokens_in > 0 and line.tokens_out > 0 and line.cost_usd is not None
    assert line.detail["request"]["thinking"] == {"type": "disabled"}
    assert line.detail["usage"]["reasoning_tokens"] == 0  # rủi ro roadmap: tên tham số tắt thinking
    flags = line.detail["segments"]["flags"]
    print(f"\n{path.name} · {model} · {elapsed:.1f}s · {line.tokens_in} → {line.tokens_out} token "
          f"(cache {line.tokens_in_cached}) · ${float(line.cost_usd):.5f} · cờ {flags} · title: {ch.title_vi}")
