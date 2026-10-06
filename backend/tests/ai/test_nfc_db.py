import importlib.util
import unicodedata
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from ai.g9_support import ds_texts, put_readings, story
from ai.support import add_term, log_rows, pool_for, segments
from app import db
from app.db import get_sessionmaker
from app.models import GlossarySuggestion, GlossaryTerm, utcnow
from fake_deepseek import FakeDeepSeek
from helpers import enqueue, seed_book

pytestmark = pytest.mark.db

NFC = unicodedata.normalize("NFC", "Lâm Phàm")
NFD = unicodedata.normalize("NFD", "Lâm Phàm")


async def test_nfd_term_vs_nfc_model_output_is_not_autofixed_and_no_miss(api):
    # Review: dst_vi NFD trong DB, model trả NFC -> trước đây bị "sửa" sang NFD và miss_count tăng
    await put_readings({"林": ["lâm"], "凡": ["phàm"]})
    book_id, chapters = await ds_texts(api, [story(1, "林凡走了。\n苏清雪来了。")])
    tid = await add_term(book_id, "林凡", NFD)
    await enqueue(book_id)
    fake = FakeDeepSeek()
    fake.responder = fake.translate_lines(lambda i, src: f"{NFC} đi rồi." if "林凡" in src else "Câu thường.")
    await pool_for(fake).run_until_idle()
    seg = next(s for s in await segments(chapters[0].id) if "林凡" in s.src)
    assert seg.dst == f"{NFC} đi rồi." and "glossary_autofixed" not in seg.flags and "glossary_miss" not in seg.flags
    async with get_sessionmaker()() as s:
        row = await s.get(GlossaryTerm, uuid.UUID(tid))
        assert (row.miss_count, row.always_send) == (0, False)


async def test_api_stores_nfc(api):
    book_id = await seed_book()
    r = await api.post(f"/api/v1/books/{book_id}/glossary",
                       json={"src_zh": "林凡", "dst_vi": NFD, "category": "character", "aliases": [NFD]})
    assert r.status_code == 201
    assert r.json()["dst_vi"] == NFC and r.json()["aliases"] == [NFC]
    r = await api.patch(f"/api/v1/glossary/{r.json()['id']}", json={"dst_vi": NFD})
    assert r.json()["term"]["dst_vi"] == NFC


def _migration():
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0010_glossary_nfc.py"
    spec = importlib.util.spec_from_file_location("mig0010", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_migration_0010_normalizes_and_keeps_first_on_duplicate(clean_db, caplog):
    book_id = await seed_book()
    compat, plain = "豈", "豈"  # NFC(compat) == plain
    async with get_sessionmaker()() as s:
        now = utcnow()
        s.add(GlossaryTerm(book_id=book_id, src_zh="林凡", dst_vi=NFD, category="character", aliases=[NFD], created_at=now))
        a = GlossaryTerm(book_id=book_id, src_zh=compat + "一", dst_vi=NFD, category="character", created_at=now)
        s.add(a)
        s.add(GlossaryTerm(book_id=book_id, src_zh=plain + "一", dst_vi="Khác", category="character", created_at=now))
        s.add(GlossarySuggestion(book_id=book_id, src_zh="苏清雪", dst_vi=NFD, category="character", confidence=90,
                                 occurrence_count=1, provider="deepseek", status="pending", created_at=now, updated_at=now))
        await s.commit()
        a_id = a.id
    mig = _migration()
    async with db.get_engine().begin() as conn:
        counts = await conn.run_sync(mig.normalize_glossary)
    assert counts["glossary_terms"] >= 2 and counts["glossary_suggestions"] == 1
    async with get_sessionmaker()() as s:
        rows = {t.src_zh: t for t in (await s.scalars(select(GlossaryTerm))).all()}
        assert rows["林凡"].dst_vi == NFC and rows["林凡"].aliases == [NFC]
        dup = next(t for t in rows.values() if t.id == a_id)
        assert dup.src_zh == compat + "一" and dup.dst_vi == NFC  # trùng src: giữ nguyên src, vẫn chuẩn hoá dst
        assert (await s.scalars(select(GlossarySuggestion.dst_vi))).one() == NFC
