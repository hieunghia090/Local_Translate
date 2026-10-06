import json

import pytest
from sqlalchemy import select, update

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.deepseek.client import DeepSeekClient
from app.deepseek.pool import DeepSeekPool
from app.models import AiModel, Chapter, GlossaryTerm, Job, WorkerState
from app.services import queue
from app.worker import Worker
from ai.support import summaries, add_term, ds_book, get, log_rows, pool_for, segments
from fake_deepseek import FAKE_KEY, FakeDeepSeek, error, reply, sent_lines, system_of
from helpers import enqueue

pytestmark = pytest.mark.db


async def _jobs() -> list[Job]:
    async with get_sessionmaker()() as s:
        return list((await s.scalars(select(Job).order_by(Job.position))).all())


async def test_chinese_output_retried_then_chapter_error(api):
    # AC-8.4
    book_id, chapters = await ds_book(api, lines=2)
    (job_id,) = await enqueue(book_id)
    fake = FakeDeepSeek()
    fake.responder = fake.translate_lines(lambda i, src: "这是中文这是中文这是中文")
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 3 and fake.sleeps == [2.0, 4.0]
    warns = [x for x in await log_rows(level="warn") if "Bản dịch vẫn còn là tiếng Trung" in x.message]
    assert len(warns) == 2
    assert (await get(Chapter, chapters[0].id)).status == "error"
    assert (await get(Job, job_id)).status == "failed"
    (err,) = await log_rows(level="error", source="translate")
    assert err.tokens_in > 0  # token của 3 lượt bị từ chối vẫn được tính tiền


async def test_two_missing_markers_resent_then_hachimi_fallback(api):
    # AC-8.5: thiếu 2 / 120 dòng
    book_id, chapters = await ds_book(api, lines=119)
    await enqueue(book_id)
    fake = FakeDeepSeek()
    fake.script(fake.translate_lines(lambda i, src: None if i in (13, 23) else f"Câu {i} đã dịch."),
                reply("⟦999⟧ không liên quan"))
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 2
    assert [i for i, _ in sent_lines(fake.requests[1])] == [13, 23]
    segs = {s.idx: s for s in await segments(chapters[0].id)}
    assert segs[13].flags == ["fallback_ct2"] and segs[13].dst == "VI<第1章第11句话。>"
    assert segs[23].flags == ["fallback_ct2"] and segs[14].flags == []
    assert (await get(Chapter, chapters[0].id)).status == "needs_review"
    assert any("thiếu 2 dòng" in x.message for x in await log_rows(level="warn"))


async def test_many_missing_markers_retry_whole_request(api):
    # G2: thiếu ≥ 5%
    book_id, chapters = await ds_book(api, lines=4)
    await enqueue(book_id)
    fake = FakeDeepSeek()
    fake.script(fake.translate_lines(lambda i, src: None if i == 3 else "Một câu."))
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 2 and fake.sleeps == [2.0]
    assert (await get(Chapter, chapters[0].id)).status == "translated"


async def test_glossary_autofix_and_miss_without_extra_request(api):
    # AC-8.6, G4, BR-8.3a
    book_id, chapters = await ds_book(api, lines=2)
    t1 = await add_term(book_id, "第1章第1", "Câu Một", aliases=["Câu Mốt"])
    t2 = await add_term(book_id, "第1章第2", "Câu Hai")
    await enqueue(book_id)
    fake = FakeDeepSeek()
    fake.responder = fake.translate_lines(lambda i, src: {3: "Câu Mốt đã dịch.", 4: "Câu Hia đã dịch."}.get(i, "Tiêu đề."))
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 1
    assert "# THUẬT NGỮ (bắt buộc dùng đúng)\n第1章第1=Câu Một\n第1章第2=Câu Hai\n" in fake.user_prompts()[0]
    segs = {s.idx: s for s in await segments(chapters[0].id)}
    assert segs[3].dst == "Câu Một đã dịch." and segs[3].flags == ["glossary_autofixed"] and segs[3].glossary_hits == [t1]
    assert segs[4].dst == "Câu Hia đã dịch." and segs[4].flags == ["glossary_miss"]
    assert (await get(Chapter, chapters[0].id)).status == "needs_review"
    async with get_sessionmaker()() as s:
        misses = {str(t.id): t.miss_count for t in (await s.scalars(select(GlossaryTerm))).all()}
    assert misses == {t1: 0, t2: 1}
    (line,) = await summaries(source="translate")
    assert line.detail["glossary"]["sent"] == 2


async def test_residual_han_flag_and_preamble_removed(api):
    # G3, G6
    book_id, chapters = await ds_book(api, lines=2)
    await enqueue(book_id)

    def respond(body):
        lines = [f"⟦{i}⟧ " + ("Hắn nói 这是什么东西" if i == 3 else f"Câu {i}.") for i, _ in sent_lines(body)]
        return reply("Đây là bản dịch của bạn:\n" + "\n".join(lines), completion=10)

    fake = FakeDeepSeek(respond)
    await pool_for(fake).run_until_idle()
    segs = {s.idx: s for s in await segments(chapters[0].id)}
    assert segs[3].flags == ["residual_han"] and segs[4].dst == "Câu 4."
    assert (await get(Chapter, chapters[0].id)).status == "needs_review"
    (line,) = await summaries(source="translate")
    assert line.detail["segments"]["preamble_removed"] == 1


async def test_401_pauses_pool_parks_jobs_cpu_keeps_running(api):
    # AC-8.7, BR-8.13, AC-5.3
    book_id, chapters = await ds_book(api, n=4, lines=1)
    await enqueue(book_id, [c.id for c in chapters[:3]])
    await enqueue(book_id, [chapters[3].id], engine="ct2")
    fake = FakeDeepSeek(lambda body: error(401, f"Authentication Fails {FAKE_KEY}"))
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 1
    ds_jobs = [j for j in await _jobs() if j.engine == "deepseek"]
    assert [j.status for j in ds_jobs] == ["paused", "paused", "paused"]
    for c in chapters[:3]:
        ch = await get(Chapter, c.id)
        assert ch.status == "queued" and ch.error is None
    ws = await get(WorkerState, "deepseek")
    assert ws.paused and ws.paused_reason == "auth"
    (err,) = await log_rows(level="error", source="system")
    assert "HTTP 401" in err.message
    dump = json.dumps([[x.message, x.detail, x.params] for x in await log_rows()], ensure_ascii=False, default=str)
    assert FAKE_KEY not in dump
    q = (await api.get(f"/api/v1/queue?book_id={book_id}")).json()
    assert q["paused"]["deepseek"] is True and q["paused_reason"]["deepseek"] == "auth"
    assert await Worker(FakeTranslator).run_once() is True  # worker HachimiMT vẫn chạy
    assert (await get(Chapter, chapters[3].id)).status == "translated"
    async with get_sessionmaker()() as s:
        await queue.set_paused(s, "deepseek", False)
        await s.commit()
    assert [j.status for j in await _jobs() if j.engine == "deepseek"] == ["queued", "queued", "queued"]
    assert (await get(WorkerState, "deepseek")).paused_reason is None


async def test_two_abnormal_token_ratios_trip_breaker(api):
    # AC-8.17, G5
    book_id, chapters = await ds_book(api, n=4, lines=1)
    await enqueue(book_id, [c.id for c in chapters[:3]])
    await enqueue(book_id, [chapters[3].id], engine="ct2")
    fake = FakeDeepSeek()
    fake.responder = fake.translate_lines(text_ratio=6.0)
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 2
    ws = await get(WorkerState, "deepseek")
    assert ws.paused and ws.paused_reason == "token_anomaly"
    assert queue.pause_message(True, ws.paused_reason) == "Token ra bất thường, đã tạm dừng để tránh tốn tiền. Xem log."
    assert [(await get(Chapter, c.id)).status for c in chapters[:3]] == ["translated", "queued", "queued"]
    assert len([x for x in await log_rows(level="warn") if "token ra bất thường" in x.message]) == 2
    assert await Worker(FakeTranslator).run_once() is True
    assert (await get(Chapter, chapters[3].id)).status == "translated"


async def test_normal_measured_ratio_does_not_trip_breaker(api):
    # G5: 3,3 lần token văn bản (đo thực tế) là bình thường
    book_id, chapters = await ds_book(api, n=4, lines=20)
    await enqueue(book_id)
    fake = FakeDeepSeek()
    fake.responder = fake.translate_lines(text_ratio=3.3)
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 4
    assert not (await get(WorkerState, "deepseek")).paused
    assert not [x for x in await log_rows(level="warn") if "token ra bất thường" in x.message]


async def test_retried_request_does_not_inflate_ratio(api):
    # lượt bị từ chối không được cộng vào tỷ lệ của lượt cuối
    book_id, chapters = await ds_book(api, n=3, lines=20)
    await enqueue(book_id)
    fake = FakeDeepSeek()
    good = fake.translate_lines(text_ratio=3.3)
    bad = lambda body: reply("không có marker", prompt=10, completion=3000)  # noqa: E731
    fake.script(bad, good, bad, good)
    fake.responder = good
    await pool_for(fake).run_until_idle()
    assert not (await get(WorkerState, "deepseek")).paused


async def test_five_failed_chapters_pause_pool(api):
    # BR-8.14
    book_id, chapters = await ds_book(api, n=6, lines=1)
    await enqueue(book_id)
    fake = FakeDeepSeek(lambda body: error(400, "bad request"))
    await pool_for(fake).run_until_idle()
    assert sorted(j.status for j in await _jobs()) == ["failed"] * 5 + ["queued"]
    assert (await get(WorkerState, "deepseek")).paused_reason == "failures"


async def test_missing_key_pauses_without_marking_chapter_error(api):
    # Review Focus 4
    book_id, chapters = await ds_book(api, lines=1)
    (job_id,) = await enqueue(book_id)
    pool = DeepSeekPool(lambda: DeepSeekClient(api_key="", base_url="https://x.test"), translator_factory=FakeTranslator)
    await pool.run_until_idle()
    assert (await get(Job, job_id)).status == "paused"
    ch = await get(Chapter, chapters[0].id)
    assert (ch.status, ch.error) == ("queued", None)
    ws = await get(WorkerState, "deepseek")
    assert ws.paused and ws.paused_reason == "no_key"
    assert "DEEPSEEK_API_KEY" in queue.pause_message(True, "no_key")


async def test_model_unavailable_fails_without_retry(api):
    # BR-8.11
    book_id, chapters = await ds_book(api, lines=1)
    await enqueue(book_id)
    fake = FakeDeepSeek(lambda body: error(404, "Model Not Exist"))
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 1 and fake.sleeps == []
    assert (await get(Chapter, chapters[0].id)).status == "error"


async def test_long_chapter_split_into_parts(api):
    # BR-8.7
    book_id, chapters = await ds_book(api, lines=60)
    async with get_sessionmaker()() as s:
        await s.execute(update(AiModel).where(AiModel.id == "deepseek-v4-pro").values(context_window=600, max_output_tokens=256))
        await s.commit()
    await enqueue(book_id)
    fake = FakeDeepSeek()
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) >= 2
    assert len({system_of(b) for b in fake.requests}) == 1
    assert all(b["max_tokens"] == 256 for b in fake.requests)
    sent = [i for b in fake.requests for i, _ in sent_lines(b)]
    rows = await segments(chapters[0].id)
    assert sorted(sent) == [s.idx for s in rows if not s.is_meta] and len(sent) == len(set(sent))
    assert (await get(Chapter, chapters[0].id)).status == "translated"
    (line,) = await summaries(source="translate")
    assert line.params["parts"] == len(fake.requests)


async def test_max_tokens_leaves_room_for_vietnamese_output(api):
    # Smoke test thật: tiếng Việt ra ≈ 3,3 lần token chữ Hán của câu gốc; max_tokens = ra × 2 theo hệ số mặc định 1,4 cắt cụt output
    from app.deepseek.estimate import Coefficients, output_tokens_est
    from app.deepseek.pool import OUT_HEADROOM, SAFE_OUT_RATIO

    book_id, _ = await ds_book(api, lines=119)
    await enqueue(book_id)
    fake = FakeDeepSeek()
    await pool_for(fake).run_until_idle()
    (body,) = fake.requests
    text = "\n".join(t for _, t in sent_lines(body))
    expected = output_tokens_est(text, Coefficients(out_ratio=SAFE_OUT_RATIO)) * OUT_HEADROOM
    assert body["max_tokens"] >= expected > 3 * output_tokens_est(text, Coefficients(out_ratio=1.0))


async def test_job_requeued_when_load_job_throws_after_claim(api, monkeypatch):
    book_id, chapters = await ds_book(api, lines=1)
    (job_id,) = await enqueue(book_id)

    async def boom(self, job_id):
        raise RuntimeError("DB tạm mất")

    monkeypatch.setattr(DeepSeekPool, "_load_job", boom)
    pool = pool_for(FakeDeepSeek())
    job = await pool.claim_next()
    assert (await get(Job, job)).status == "running"
    await pool.run_job(job)
    assert (await get(Job, job_id)).status == "queued"
    assert (await get(Chapter, chapters[0].id)).status == "queued"
    monkeypatch.undo()
    await pool_for(FakeDeepSeek()).run_until_idle()  # chạy lại được
    assert (await get(Chapter, chapters[0].id)).status == "translated"
