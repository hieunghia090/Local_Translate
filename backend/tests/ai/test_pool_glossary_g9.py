import uuid

import pytest
from sqlalchemy import select

from ai.g9_support import ds_texts, is_extract, jobs, put_readings, story, suggestions, term, terms_reply
from ai.support import add_term, get, log_rows, pool_for, segments
from app.db import get_sessionmaker
from app.models import Chapter, GlossaryTerm
from fake_deepseek import FakeDeepSeek, error, sent_lines, user_of
from helpers import enqueue

pytestmark = pytest.mark.db

GLOSS = "# THUẬT NGỮ (bắt buộc dùng đúng)\n"


def glossary_block(body: dict) -> str:
    user = user_of(body)
    return user.split(GLOSS, 1)[1].split("\n\n", 1)[0] if GLOSS in user else ""


async def _term(api, book_id, src, dst, **kw) -> dict:
    r = await api.post(f"/api/v1/books/{book_id}/glossary", json={"src_zh": src, "dst_vi": dst, "category": "character", **kw})
    assert r.status_code == 201, r.text
    return r.json()


def by_src(fn):
    """translate_lines: dòng nguồn chứa khoá nào thì trả bản dịch tương ứng."""
    def pick(i, src):
        return next((out for key, out in fn.items() if key in src), "Câu thường.")
    return pick


async def test_ac_8_12_predictable_term_not_sent_but_autofixed(api):
    await put_readings({"林": ["lâm"], "凡": ["phàm"]})
    book_id, chapters = await ds_texts(api, [story(1, "林凡走了。\n苏清雪来了。")])
    t = await _term(api, book_id, "林凡", "Lâm Phàm", aliases=["Lâm Phạm"])
    assert t["predictable"] is True
    await enqueue(book_id)
    fake = FakeDeepSeek()
    fake.responder = fake.translate_lines(by_src({"林凡": "Lâm Phạm đi rồi."}))
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 1 and "林凡=" not in user_of(fake.requests[0])
    seg = next(s for s in await segments(chapters[0].id) if "林凡" in s.src)
    assert seg.dst == "Lâm Phàm đi rồi." and seg.flags == ["glossary_autofixed"]
    (line,) = [x for x in await log_rows(source="translate") if "glossary" in (x.detail or {})]  # bỏ dòng log từng request
    assert (line.detail["glossary"]["skipped_predictable"], line.detail["glossary"]["sent"]) == (1, 0)
    async with get_sessionmaker()() as s:
        assert (await s.get(GlossaryTerm, uuid.UUID(t["id"]))).miss_count == 1  # bị L3 bỏ mà model dịch khác


async def test_hanviet_variant_autofix_without_alias(api):
    await put_readings({"外": ["ngoại"], "门": ["môn"]})
    book_id, chapters = await ds_texts(api, [story(1, "他进了外门。")])
    await _term(api, book_id, "外门", "Ngoại môn đệ tử", category="organization")
    await enqueue(book_id)
    fake = FakeDeepSeek()
    fake.responder = fake.translate_lines(by_src({"外门": "Hắn vào ngoại môn."}))
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 1
    seg = next(s for s in await segments(chapters[0].id) if "外门" in s.src)
    assert (seg.dst, seg.flags) == ("Hắn vào Ngoại môn đệ tử.", ["glossary_autofixed"])


async def test_always_send_after_three_misses(api):
    # spec 08 G4, BR-4.18
    await put_readings({"林": ["lâm"], "凡": ["phàm"]})
    book_id, chapters = await ds_texts(api, [story(i, "林凡走了。") for i in range(1, 5)])
    t = await _term(api, book_id, "林凡", "Lâm Phàm")
    fake = FakeDeepSeek()
    fake.responder = fake.translate_lines(by_src({"林凡": "Lâm Phạn đi rồi."}))
    await enqueue(book_id, [c.id for c in chapters[:3]])
    await pool_for(fake).run_until_idle()
    assert all(GLOSS not in user_of(b) for b in fake.requests)
    async with get_sessionmaker()() as s:
        row = await s.get(GlossaryTerm, uuid.UUID(t["id"]))
        assert (row.miss_count, row.always_send) == (3, True)
    assert any("Tự bật “Luôn gửi”" in x.message and "林凡" in x.message for x in await log_rows(source="glossary"))
    await enqueue(book_id, [chapters[3].id])
    await pool_for(fake).run_until_idle()
    assert glossary_block(fake.requests[-1]) == "林凡=Lâm Phàm"


async def test_ac_8_14_cap_120_lines_and_g4_checks_the_rest(api):
    names = [chr(0x5200 + 2 * i) + chr(0x5201 + 2 * i) for i in range(200)]
    lines = ["，".join(n * (2 if i < 120 else 1) for i, n in enumerate(names[k:k + 20], start=k)) + "。" for k in range(0, 200, 20)]
    book_id, chapters = await ds_texts(api, [story(1, "\n".join(lines))])
    for i, n in enumerate(names):
        await add_term(book_id, n, f"Từ {i}")
    await enqueue(book_id)
    fake = FakeDeepSeek()
    await pool_for(fake).run_until_idle()
    block = glossary_block(fake.requests[0]).split("\n")
    assert len(block) == 120 and {b.split("=")[1] for b in block} == {f"Từ {i}" for i in range(120)}
    last = next(s for s in await segments(chapters[0].id) if names[199] in s.src)
    assert "glossary_miss" in last.flags  # term bị cắt vẫn được G4 kiểm tra
    async with get_sessionmaker()() as s:
        assert {t.miss_count for t in (await s.scalars(select(GlossaryTerm))).all()} == {1}
    (line,) = [x for x in await log_rows(source="translate") if "glossary" in (x.detail or {})]  # bỏ dòng log từng request
    assert (line.detail["glossary"]["sent"], line.detail["glossary"]["truncated"]) == (120, 80)


async def test_ac_8_15_identical_glossary_block_across_chapters(api):
    await put_readings({"林": ["lâm"], "凡": ["phàm"]})
    book_id, _ = await ds_texts(api, [story(1, "高俅走进外门。林凡来了。"), story(2, "外门里，高俅说话。")])
    await _term(api, book_id, "高俅", "Cao Cầu")
    await _term(api, book_id, "外门", "Ngoại môn đệ tử", category="term")
    await _term(api, book_id, "林凡", "Lâm Phàm")
    await enqueue(book_id)
    fake = FakeDeepSeek()
    await pool_for(fake).run_until_idle()
    a, b = (glossary_block(r) for r in fake.requests)
    assert a.encode() == b.encode() == "外门=Ngoại môn đệ tử\n高俅=Cao Cầu".encode()


async def test_ac_8_13_send_stats_after_twenty_chapters(api):
    await put_readings({"林": ["lâm"], "凡": ["phàm"]})
    book_id, _ = await ds_texts(api, [story(i, "林凡走进外门。") for i in range(1, 21)], concurrency=3)
    await _term(api, book_id, "林凡", "Lâm Phàm")
    await _term(api, book_id, "外门", "Ngoại môn đệ tử", category="term")
    await enqueue(book_id)
    fake = FakeDeepSeek()
    fake.responder = fake.translate_lines(by_src({"林凡": "Lâm Phàm vào cửa."}))
    await pool_for(fake).run_until_idle()
    st = (await api.get(f"/api/v1/books/{book_id}/glossary/send-stats")).json()
    assert st["chapters"] == 20 and st["avg_tokens_est"] > 0
    assert (st["avg_sent"], st["avg_skipped_predictable"], st["avg_truncated"]) == (1.0, 1.0, 0.0)
    assert st["miss_pct"] == 50.0  # mỗi chương 2 câu (tiêu đề + 1 câu), câu có 外门 bị miss


async def test_br_8_17_auto_extract_after_deepseek_chapter(api):
    book_id, chapters = await ds_texts(api, [story(1, "苏清雪来了。苏清雪走了。")], auto_extract=True)
    await enqueue(book_id)
    fake = FakeDeepSeek()
    base = fake.translate_lines()
    fake.responder = lambda body: terms_reply(term("苏清雪", "Tô Thanh Tuyết")) if is_extract(body) else base(body)
    await pool_for(fake).run_until_idle()
    (job,) = await jobs("ai_extract")
    assert (job.status, job.options["auto"], job.options["chapter_ids"]) == ("done", True, [str(chapters[0].id)])
    assert [s.src_zh for s in await suggestions(book_id, "pending")] == ["苏清雪"]
    assert (await get(Chapter, chapters[0].id)).status == "translated"


async def test_auto_extract_skips_chapters_already_scanned(api):
    # Review: dịch lại chương đã quét không được xếp thêm job trích (tốn tiền)
    book_id, chapters = await ds_texts(api, [story(1, "苏清雪来了。苏清雪走了。")], auto_extract=True)
    fake = FakeDeepSeek()
    base = fake.translate_lines()
    fake.responder = lambda body: terms_reply(term("苏清雪", "Tô Thanh Tuyết")) if is_extract(body) else base(body)
    await enqueue(book_id)
    await pool_for(fake).run_until_idle()
    assert len(await jobs("ai_extract")) == 1 and (await get(Chapter, chapters[0].id)).ai_scanned_at is not None
    await enqueue(book_id)  # dịch lại cùng chương
    await pool_for(fake).run_until_idle()
    assert len(await jobs("ai_extract")) == 1
    extract_requests = [b for b in fake.requests if is_extract(b)]
    assert len(extract_requests) == 1


async def test_br_8_17_extract_failure_does_not_touch_chapter(api):
    book_id, chapters = await ds_texts(api, [story(1, "苏清雪来了。苏清雪走了。")], auto_extract=True)
    await enqueue(book_id)
    fake = FakeDeepSeek()
    base = fake.translate_lines()
    fake.responder = lambda body: error(500) if is_extract(body) else base(body)
    await pool_for(fake).run_until_idle()
    (job,) = await jobs("ai_extract")
    assert job.status == "failed"
    ch = await get(Chapter, chapters[0].id)
    assert (ch.status, ch.error) == ("translated", None)


async def test_user_turning_always_send_off_resets_miss_count_and_sticks(api):
    # Review: miss_count vẫn đếm nên "Luôn gửi" tự bật lại ngay ở lần miss tiếp theo
    await put_readings({"林": ["lâm"], "凡": ["phàm"]})
    book_id, chapters = await ds_texts(api, [story(1, "林凡走了。")])
    t = await _term(api, book_id, "林凡", "Lâm Phàm")
    async with get_sessionmaker()() as s:
        row = await s.get(GlossaryTerm, uuid.UUID(t["id"]))
        row.miss_count, row.always_send = 5, True
        await s.commit()
    r = await api.patch(f"/api/v1/glossary/{t['id']}", json={"always_send": False})
    assert r.json()["term"]["miss_count"] == 0 and r.json()["term"]["always_send"] is False
    fake = FakeDeepSeek()
    fake.responder = fake.translate_lines(by_src({"林凡": "Lâm Phạn đi rồi."}))
    await enqueue(book_id)
    await pool_for(fake).run_until_idle()
    async with get_sessionmaker()() as s:
        row = await s.get(GlossaryTerm, uuid.UUID(t["id"]))
        assert (row.miss_count, row.always_send) == (1, False)


async def test_editing_dst_resets_miss_count_but_unrelated_edits_do_not(api):
    await put_readings({"林": ["lâm"], "凡": ["phàm"]})
    book_id, _ = await ds_texts(api, [story(1, "林凡走了。")])
    t = await _term(api, book_id, "林凡", "Lâm Phàm")
    async with get_sessionmaker()() as s:
        (await s.get(GlossaryTerm, uuid.UUID(t["id"]))).miss_count = 2
        await s.commit()
    assert (await api.patch(f"/api/v1/glossary/{t['id']}", json={"notes": "x"})).json()["term"]["miss_count"] == 2
    assert (await api.patch(f"/api/v1/glossary/{t['id']}", json={"dst_vi": "Lâm Phạm"})).json()["term"]["miss_count"] == 0
