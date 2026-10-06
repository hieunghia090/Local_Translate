import importlib.util
import time
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text, update

from app import db
from app.db import get_sessionmaker
from app.models import Chapter, ChapterRevision, Segment, utcnow
from helpers import seed_book

pytestmark = pytest.mark.db


def _migration():
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0011_engine_versions.py"
    spec = importlib.util.spec_from_file_location("mig0011", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _backfill() -> dict:
    async with db.get_engine().begin() as conn:
        return await conn.run_sync(_migration().backfill)


async def _chapter(book_id, no: int, model_id: str | None, rows, status: str = "translated"):
    """rows: [(idx, src, dst_machine, is_meta)]; dst = dst_machine, hai cột mới để NULL như dữ liệu trước 0011."""
    async with get_sessionmaker()() as s:
        ch = Chapter(book_id=book_id, no=no, title_zh=f"第{no}章", status=status, char_count=10, source_hash="x",
                     model_id=model_id)
        s.add(ch)
        await s.flush()
        for idx, src, machine, is_meta in rows:
            s.add(Segment(chapter_id=ch.id, idx=idx, src=src, is_meta=is_meta, dst=machine, dst_machine=machine))
        await s.commit()
        return ch.id


async def _revision(chapter_id, model_id: str, items, *, note: str | None = None, minutes_ago: int = 0):
    at = utcnow() - timedelta(minutes=minutes_ago)
    async with get_sessionmaker()() as s:
        s.add(ChapterRevision(
            chapter_id=chapter_id, kind="machine", model_id=model_id, note=note, changed_idx=[], created_at=at, updated_at=at,
            snapshot=[{"idx": i, "src": src, "dst": d, "dst_machine": d, "edited": False} for i, src, d in items]))
        await s.commit()


async def _versions(chapter_id) -> dict[int, tuple]:
    async with get_sessionmaker()() as s:
        rows = (await s.scalars(select(Segment).where(Segment.chapter_id == chapter_id).order_by(Segment.idx))).all()
    return {r.idx: (r.dst_mt, r.dst_ai) for r in rows}


async def test_current_engine_column_comes_from_dst_machine(clean_db):
    book_id = await seed_book()
    mt = await _chapter(book_id, 1, "HachimiMT-60", [(0, "第1章", "Chương 1", False), (1, "=====", "=====", True),
                                                     (2, "他走了。", "Hắn đi rồi.", False)])
    ai = await _chapter(book_id, 2, "deepseek-v4-pro", [(0, "第2章", "Chương hai", False)])
    legacy = await _chapter(book_id, 3, None, [(0, "第3章", "Chương 3", False)])
    await _chapter(book_id, 4, None, [], status="todo")
    counts = await _backfill()
    assert await _versions(mt) == {0: ("Chương 1", None), 1: (None, None), 2: ("Hắn đi rồi.", None)}
    assert await _versions(ai) == {0: (None, "Chương hai")}
    assert await _versions(legacy) == {0: ("Chương 3", None)}
    assert (counts["current_mt"], counts["current_ai"]) == (3, 1)


async def test_other_engine_from_latest_translation_revision_with_same_src(clean_db):
    book_id = await seed_book()
    cid = await _chapter(book_id, 1, "deepseek-v4-pro", [(0, "第1章", "AI 0", False), (1, "他走了。", "AI 1", False),
                                                         (2, "新句子。", "AI 2", False)])
    await _revision(cid, "HachimiMT-60", [(0, "第1章", "MT cũ 0"), (1, "他走了。", "MT cũ 1")], minutes_ago=30)
    await _revision(cid, "HachimiMT-60", [(0, "第1章", "MT 0"), (1, "他走了。", "MT 1"), (2, "旧句子。", "MT 2")], minutes_ago=20)
    await _revision(cid, "HachimiMT-60", [(0, "第1章", "Trước khi thay gốc")], note="Bản dịch trước khi thay bản gốc",
                    minutes_ago=15)
    await _revision(cid, "deepseek-v4-pro", [(0, "第1章", "AI 0")], minutes_ago=10)
    await _backfill()
    # câu 2: src trong snapshot khác src hiện tại -> không ghép
    assert await _versions(cid) == {0: ("MT 0", "AI 0"), 1: ("MT 1", "AI 1"), 2: (None, "AI 2")}


async def test_review_revision_is_not_ai_output(clean_db):
    # Review Focus 5: revision soát mang model DeepSeek nhưng nội dung là bản HachimiMT đã áp fix
    book_id = await seed_book()
    cid = await _chapter(book_id, 1, "HachimiMT-60", [(0, "第1章", "MT đã áp fix", False)])
    await _revision(cid, "deepseek-v4-pro", [(0, "第1章", "AI thật")], minutes_ago=30)
    await _revision(cid, "HachimiMT-60", [(0, "第1章", "MT gốc")], minutes_ago=20)
    await _revision(cid, "deepseek-flash", [(0, "第1章", "MT đã áp fix")], note="review", minutes_ago=10)
    await _backfill()
    assert await _versions(cid) == {0: ("MT đã áp fix", "AI thật")}


async def test_backfill_is_idempotent_and_never_overwrites(clean_db):
    book_id = await seed_book()
    cid = await _chapter(book_id, 1, "HachimiMT-60", [(0, "第1章", "MT", False)])
    await _backfill()
    async with get_sessionmaker()() as s:
        await s.execute(update(Segment).where(Segment.chapter_id == cid).values(dst_ai="giữ nguyên"))
        await s.commit()
    await _revision(cid, "deepseek-v4-pro", [(0, "第1章", "AI khác")])
    counts = await _backfill()
    assert await _versions(cid) == {0: ("MT", "giữ nguyên")}
    assert counts == {"current_mt": 0, "current_ai": 0, "other_mt": 0, "other_ai": 0}


async def test_backfill_700_chapters_is_fast(clean_db):
    book_id = await seed_book()
    async with db.get_engine().begin() as conn:
        await conn.execute(text("""
            INSERT INTO chapters (id, book_id, no, title_zh, status, char_count, source_hash, model_id)
            SELECT gen_random_uuid(), CAST(:b AS uuid), n, 'x', 'translated'::chapter_status, 10, 'x',
                   CASE WHEN n % 2 = 0 THEN 'deepseek-v4-pro' ELSE 'HachimiMT-60' END
            FROM generate_series(1, 700) n"""), {"b": book_id})
        await conn.execute(text("""
            INSERT INTO segments (chapter_id, idx, src, is_meta, dst, dst_machine)
            SELECT c.id, i, 'src ' || i, false, 'dst ' || i, 'dst ' || i
            FROM chapters c CROSS JOIN generate_series(0, 99) i WHERE c.book_id = :b"""), {"b": book_id})
        await conn.execute(text("""
            INSERT INTO chapter_revisions (id, chapter_id, kind, model_id, segments_changed, snapshot, changed_idx,
                                           created_at, updated_at)
            SELECT gen_random_uuid(), c.id, 'machine'::revision_kind,
                   CASE WHEN c.model_id LIKE 'deepseek%' THEN 'HachimiMT-60' ELSE 'deepseek-v4-pro' END, 100,
                   (SELECT jsonb_agg(jsonb_build_object('idx', i, 'src', 'src ' || i, 'dst', 'old ' || i,
                                                        'dst_machine', 'old ' || i, 'edited', false))
                    FROM generate_series(0, 99) i),
                   '[]'::jsonb, now() - interval '1 hour', now() - interval '1 hour'
            FROM chapters c WHERE c.book_id = :b"""), {"b": book_id})
    t0 = time.perf_counter()
    counts = await _backfill()
    elapsed = time.perf_counter() - t0
    print(f"backfill 0011 · 70.000 câu · {elapsed * 1000:.0f} ms")
    assert counts["current_mt"] + counts["current_ai"] == 70_000
    assert counts["other_mt"] + counts["other_ai"] == 70_000
    assert elapsed < 10.0
