import pytest
from sqlalchemy import update

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.models import Segment
from app.worker import Worker
from ai.support import pool_for, segments
from fake_deepseek import FakeDeepSeek, reply
from helpers import enqueue, make_book

pytestmark = pytest.mark.db


async def _book(api, lines: int = 3):
    book_id, chapters = await make_book(api, n_chapters=1, lines=lines)
    r = await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"deepseek": {"auto_extract_glossary": False}}})
    assert r.status_code == 200, r.text
    return book_id, chapters[0]


async def _ct2(book_id, prefix: str = "VI", kind: str = "translate", **kw):
    await enqueue(book_id, kind=kind, **kw)
    assert await Worker(lambda: FakeTranslator(prefix=prefix)).run_once()


async def _deepseek(book_id, fake: FakeDeepSeek | None = None, **kw):
    await enqueue(book_id, kind="retranslate", engine="deepseek", **kw)
    await pool_for(fake or FakeDeepSeek()).run_until_idle()


def _text(rows):
    return [s for s in rows if not s.is_meta]


async def test_ct2_writes_dst_mt_only(api):
    # BR-6.10
    book_id, ch = await _book(api)
    await _ct2(book_id)
    rows = await segments(ch.id)
    for s in rows:
        if s.is_meta:
            assert (s.dst_mt, s.dst_ai) == (None, None)
        else:
            assert s.dst_mt == s.dst_machine == s.dst == f"VI<{s.src}>" and s.dst_ai is None


async def test_ct2_retranslate_keeps_dst_ai_unless_src_changed(api):
    # Review Focus 1
    book_id, ch = await _book(api)
    await _ct2(book_id)
    async with get_sessionmaker()() as s:  # giả lập đã có bản AI từ trước
        await s.execute(update(Segment).where(Segment.chapter_id == ch.id, Segment.is_meta.is_(False))
                        .values(dst_ai="AI cũ"))
        await s.execute(update(Segment).where(Segment.chapter_id == ch.id, Segment.idx == 3).values(src="câu gốc khác"))
        await s.commit()
    await _ct2(book_id, prefix="V2", kind="retranslate")
    by = {s.idx: s for s in _text(await segments(ch.id))}
    assert by[3].dst_ai is None  # src trước đó khác bản gốc thật -> không giữ
    assert all(s.dst_ai == "AI cũ" for i, s in by.items() if i != 3)
    assert all(s.dst_mt == s.dst_machine == f"V2<{s.src}>" for s in by.values())


async def test_ct2_then_deepseek_then_ct2(api):
    # AC-6.12
    book_id, ch = await _book(api)
    await _ct2(book_id)
    await _deepseek(book_id)
    for s in _text(await segments(ch.id)):
        assert s.dst == s.dst_machine == s.dst_ai == f"Câu {s.idx} đã dịch."
        assert s.dst_mt == f"VI<{s.src}>"
    await _ct2(book_id, prefix="V2", kind="retranslate")
    for s in _text(await segments(ch.id)):
        assert s.dst == s.dst_machine == s.dst_mt == f"V2<{s.src}>"
        assert s.dst_ai == f"Câu {s.idx} đã dịch."
    assert all((s.dst_mt, s.dst_ai) == (None, None) for s in await segments(ch.id) if s.is_meta)


async def test_deepseek_keep_manual_edit_still_refreshes_dst_ai(api):
    book_id, ch = await _book(api)
    await _ct2(book_id)
    r = await api.patch(f"/api/v1/segments/{ch.id}/3", json={"dst": "Tôi sửa."})
    assert r.status_code == 200
    await _deepseek(book_id, options={"keep_manual_edits": True})
    by = {s.idx: s for s in await segments(ch.id)}
    assert (by[3].dst, by[3].edited) == ("Tôi sửa.", True)
    assert by[3].dst_ai == by[3].dst_machine == "Câu 3 đã dịch." and by[3].dst_mt == f"VI<{by[3].src}>"


async def test_deepseek_fallback_line_stays_in_dst_ai_with_flag(api):
    # BR-6.10: dòng DeepSeek bỏ sót, dịch bằng HachimiMT, vẫn là bản AI của lần chạy này.
    # Cùng cách dựng với test_two_missing_markers_resent_then_hachimi_fallback (G8): thiếu 1/120 dòng (< 5%) thì gửi lại.
    book_id, ch = await _book(api, lines=119)
    await _ct2(book_id, prefix="MT")
    fake = FakeDeepSeek()
    fake.script(fake.translate_lines(lambda i, src: None if i == 13 else f"Câu {i} đã dịch."),
                reply("⟦999⟧ không liên quan"))
    await _deepseek(book_id, fake)
    by = {s.idx: s for s in await segments(ch.id)}
    assert by[13].flags == ["fallback_ct2"] and by[13].dst_ai == by[13].dst_machine == f"VI<{by[13].src}>"
    assert by[13].dst_mt == f"MT<{by[13].src}>"
    assert by[14].dst_ai == "Câu 14 đã dịch."


async def test_worker_column_follows_translator_model_id(api):
    # BR-6.12: cột ghi theo model_id đã lưu vào chương, không theo tên engine của worker
    book_id, ch = await _book(api)
    tr = FakeTranslator(prefix="VI")
    tr.model_id = "deepseek-flash"
    await enqueue(book_id)
    assert await Worker(lambda: tr).run_once()
    for s in _text(await segments(ch.id)):
        assert s.dst_ai == s.dst_machine and s.dst_mt is None
