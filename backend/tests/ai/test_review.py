import json
import uuid

import pytest
from sqlalchemy import select

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.models import AiModel, Chapter, Job
from app.worker import Worker
from ai.support import get, log_rows, pool_for, segments
from fake_deepseek import FakeDeepSeek, reply
from helpers import enqueue, make_book

pytestmark = pytest.mark.db


def fixes_reply(*fixes) -> object:
    return reply(json.dumps({"fixes": list(fixes)}, ensure_ascii=False), prompt=1000, completion=100)


async def ct2_translated(api, *, lines: int = 3, cfg: dict | None = None):
    """Chương đã dịch bằng HachimiMT (giả, model_id = "fake")."""
    book_id, chapters = await make_book(api, n_chapters=1, lines=lines)
    if cfg:
        assert (await api.patch(f"/api/v1/books/{book_id}", json={"run_config": cfg})).status_code == 200
    await enqueue(book_id)
    assert await Worker(FakeTranslator).run_once()
    return book_id, chapters[0]


async def pending(api, chapter_id) -> list[dict]:
    return (await api.get(f"/api/v1/chapters/{chapter_id}/review-fixes?status=pending")).json()["items"]


async def test_review_three_fixes_apply_two_reject_one_then_rereview(api):
    # AC-8.9, AC-8.16, BR-8.19, BR-8.21
    book_id, ch = await ct2_translated(api, lines=3)
    segs = {s.idx: s for s in await segments(ch.id)}
    fixes = [{"idx": i, "type": "mistranslation", "before": segs[i].dst, "after": f"Câu {i} sửa.", "reason": "sai nghĩa",
              "confidence": 80} for i in (3, 4, 5)]
    r = await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    assert r.status_code == 202, r.text
    fake = FakeDeepSeek()
    fake.script(fixes_reply(*fixes))
    await pool_for(fake).run_until_idle()

    body = fake.requests[0]
    assert body["thinking"] == {"type": "disabled"} and body["response_format"] == {"type": "json_object"}
    assert body["model"] == "deepseek-flash" and body["messages"][0]["content"].startswith("Bạn là biên tập viên")
    listed = await pending(api, ch.id)
    assert [f["segment_idx"] for f in listed] == [3, 4, 5]
    detail = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()
    assert detail["chapter"]["status"] == "needs_review" and detail["chapter"]["model_id"] == "fake"
    assert "review_pending" in detail["segments"][3]["flags"]

    by_idx = {f["segment_idx"]: f["id"] for f in listed}
    applied = (await api.post("/api/v1/review-fixes/apply", json={"ids": [by_idx[3], by_idx[4]]})).json()
    assert applied == {"applied": 2, "stale": 0, "skipped": 0}
    assert (await api.post("/api/v1/review-fixes/reject", json={"ids": [by_idx[5]]})).json() == {"rejected": 1}
    after = {s["idx"]: s for s in (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()["segments"]}
    assert after[3]["dst"] == "Câu 3 sửa." and after[4]["dst"] == "Câu 4 sửa." and after[5]["dst"] == segs[5].dst
    assert not after[3]["edited"] and after[3]["dst_machine"] == "Câu 3 sửa."
    assert all("review_pending" not in after[i]["flags"] for i in (3, 4, 5))
    revs = (await api.get(f"/api/v1/chapters/{ch.id}/revisions")).json()
    assert (revs[0]["kind"], revs[0]["note"], revs[0]["segments_changed"], revs[0]["model_id"]) == ("machine", "review", 2, "deepseek-flash")

    # Soát lại: DeepSeek đề xuất lại đúng 3 fix cũ, không fix nào được đề xuất lại
    await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    fake.script(fixes_reply(*fixes))
    await pool_for(fake).run_until_idle()
    assert await pending(api, ch.id) == []
    line = (await log_rows(source="review"))[-1]
    assert line.detail["review"]["dropped_rejected"] == 1 and line.detail["review"]["dropped_mismatch"] == 2
    assert line.detail["request"]["thinking"] == {"type": "disabled"}  # AC-8.16: xem được trong log chi tiết
    assert line.tokens_in == 1000 and float(line.cost_usd) > 0


async def test_high_confidence_auto_apply(api):
    # BR-8.20
    book_id, ch = await ct2_translated(api, lines=2, cfg={"review": {"auto_apply": "high_confidence"}})
    segs = {s.idx: s for s in await segments(ch.id)}
    await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    fake = FakeDeepSeek()
    fake.script(fixes_reply(
        {"idx": 3, "type": "name_mismatch", "before": segs[3].dst, "after": "Cao Cầu nói.", "reason": "tên", "confidence": 95},
        {"idx": 4, "type": "mistranslation", "before": segs[4].dst, "after": "Câu bốn.", "reason": "nghĩa", "confidence": 99},
    ))
    await pool_for(fake).run_until_idle()
    rows = {s.idx: s for s in await segments(ch.id)}
    assert rows[3].dst == "Cao Cầu nói." and "review_pending" not in rows[3].flags
    assert rows[4].dst == segs[4].dst and "review_pending" in rows[4].flags
    assert [f["segment_idx"] for f in await pending(api, ch.id)] == [4]
    assert (await get(Chapter, ch.id)).status == "needs_review"


async def test_manual_edits_win_over_review(api):
    # Review Focus 5, BR-8.22
    book_id, ch = await ct2_translated(api, lines=2)
    segs = {s.idx: s for s in await segments(ch.id)}
    await api.patch(f"/api/v1/segments/{ch.id}/3", json={"dst": "Tôi sửa câu ba."})
    await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    fake = FakeDeepSeek()
    fake.script(fixes_reply(
        {"idx": 3, "type": "grammar", "before": "Tôi sửa câu ba.", "after": "X.", "reason": "r", "confidence": 99},
        {"idx": 4, "type": "grammar", "before": segs[4].dst, "after": "Câu 4 sửa.", "reason": "r", "confidence": 70},
    ))
    await pool_for(fake).run_until_idle()
    assert "⟦3⟧ [KHÔNG SỬA]" in fake.user_prompts()[0]
    listed = await pending(api, ch.id)
    assert [f["segment_idx"] for f in listed] == [4]
    assert (await log_rows(source="review"))[-1].detail["review"]["dropped_locked"] == 1
    # sửa tay câu 4 sau khi đã có đề xuất, rồi áp đề xuất cũ: không ghi đè
    await api.patch(f"/api/v1/segments/{ch.id}/4", json={"dst": "Tôi sửa câu bốn."})
    r = (await api.post("/api/v1/review-fixes/apply", json={"ids": [listed[0]["id"]]})).json()
    assert r == {"applied": 0, "stale": 1, "skipped": 0}
    assert {s.idx: s.dst for s in await segments(ch.id)}[4] == "Tôi sửa câu bốn."


async def test_auto_review_after_ct2(api):
    # BR-8.23
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"review": {"auto_after_ct2": True}}})
    await enqueue(book_id)
    await Worker(FakeTranslator).run_once()
    async with get_sessionmaker()() as s:
        jobs = {j.kind: j for j in (await s.scalars(select(Job))).all()}
    assert jobs["translate"].status == "done"
    assert (jobs["review"].engine, jobs["review"].status, jobs["review"].prev_chapter_status) == ("deepseek", "queued", None)


async def test_cancel_queued_review_keeps_chapter_status(api):
    book_id, ch = await ct2_translated(api)
    job_id = (await api.post(f"/api/v1/chapters/{ch.id}/ai-review")).json()["job_id"]
    assert (await get(Chapter, ch.id)).status == "translated"  # soát không đổi trạng thái chương
    assert (await api.delete(f"/api/v1/jobs/{job_id}")).status_code == 200
    assert (await get(Chapter, ch.id)).status == "translated"
    assert (await get(Job, uuid.UUID(job_id))).status == "cancelled"


async def test_ai_review_rejects_untranslated_or_busy(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    r = await api.post(f"/api/v1/chapters/{chapters[0].id}/ai-review")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHAPTER_NOT_REVIEWABLE"
    book_id, ch = await ct2_translated(api)
    await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    r = await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    assert r.status_code == 409 and r.json()["error"]["code"] == "CHAPTER_BUSY"


async def test_invalid_review_json_retried(api):
    book_id, ch = await ct2_translated(api, lines=1)
    await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    fake = FakeDeepSeek()
    fake.script(reply("không phải JSON"), fixes_reply())
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 2 and fake.sleeps == [2.0]
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(Job.status).where(Job.kind == "review"))).one() == "done"


async def test_review_max_tokens_scales_with_line_count(api):
    # G5: max_tokens = min(max_output, max(1024, 250 × ceil(0.3 × số dòng)))
    book_id, ch = await ct2_translated(api, lines=119)
    await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    fake = FakeDeepSeek()
    fake.script(fixes_reply())
    await pool_for(fake).run_until_idle()
    n = len([s for s in await segments(ch.id) if not s.is_meta])
    assert n == 120
    async with get_sessionmaker()() as s:
        flash = await s.get(AiModel, "deepseek-flash")
    assert fake.requests[0]["max_tokens"] == min(flash.max_output_tokens, 9000)


async def test_review_max_tokens_has_floor_and_ceiling(api):
    from app.deepseek.estimate import review_max_tokens

    assert review_max_tokens(3, 384_000) == 1024
    assert review_max_tokens(120, 384_000) == 9000
    assert review_max_tokens(120, 4096) == 4096


async def test_truncated_review_fails_without_resend_and_logs_usage(api):
    book_id, ch = await ct2_translated(api, lines=3)
    await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    fake = FakeDeepSeek()
    fake.script(reply('{"fixes": [{"idx"', prompt=700, completion=1024, finish_reason="length"))
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 1 and fake.sleeps == []
    async with get_sessionmaker()() as s:
        job = (await s.scalars(select(Job).where(Job.kind == "review"))).one()
    assert job.status == "failed" and "cắt cụt" in job.error
    (err,) = await log_rows(level="error", source="review")
    assert (err.tokens_in, err.tokens_out) == (700, 1024) and float(err.cost_usd) > 0
    assert (await pending(api, ch.id)) == []


async def test_applied_fix_survives_honorific_reapply(api):
    from sqlalchemy import update

    book_id, ch = await ct2_translated(api, lines=3)
    async with get_sessionmaker()() as s:  # chỉ chương HachimiMT-60 mới được áp lại xưng hô
        await s.execute(update(Chapter).where(Chapter.id == ch.id).values(model_id="HachimiMT-60"))
        await s.commit()
    segs = {s.idx: s for s in await segments(ch.id)}
    fix = {"idx": 3, "type": "mistranslation", "before": segs[3].dst, "after": "Câu ba đã sửa.", "reason": "sai", "confidence": 80}
    await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    fake = FakeDeepSeek()
    fake.script(fixes_reply(fix))
    await pool_for(fake).run_until_idle()
    (f,) = await pending(api, ch.id)
    assert (await api.post("/api/v1/review-fixes/apply", json={"ids": [f["id"]]})).json()["applied"] == 1
    after = {s.idx: s for s in await segments(ch.id)}
    assert after[3].dst_model_raw == "Câu ba đã sửa." and after[3].honorific_edits == []

    r = await api.post(f"/api/v1/books/{book_id}/honorific/reapply", json={"chapter_ids": [str(ch.id)]})
    assert r.status_code == 202, r.text
    final = {s.idx: s for s in await segments(ch.id)}
    assert final[3].dst == "Câu ba đã sửa." and final[3].dst_machine == "Câu ba đã sửa."
    assert final[4].dst == segs[4].dst


async def _pending_fix(api):
    book_id, ch = await ct2_translated(api, lines=3)
    segs = {s.idx: s for s in await segments(ch.id)}
    fix = {"idx": 3, "type": "mistranslation", "before": segs[3].dst, "after": "Câu ba sửa.", "reason": "sai", "confidence": 80}
    await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    fake = FakeDeepSeek()
    fake.script(fixes_reply(fix))
    await pool_for(fake).run_until_idle()
    assert len(await pending(api, ch.id)) == 1
    return book_id, ch


async def _all_fixes(api, ch):
    return (await api.get(f"/api/v1/chapters/{ch.id}/review-fixes")).json()["items"]


async def test_hachimi_retranslate_supersedes_pending_fixes(api):
    book_id, ch = await _pending_fix(api)
    await enqueue(book_id, kind="retranslate")
    assert await Worker(FakeTranslator).run_once()
    (f,) = await _all_fixes(api, ch)
    assert (f["status"], f["reason"]) == ("rejected", "superseded")
    assert not any("review_pending" in s.flags for s in await segments(ch.id))


async def test_deepseek_retranslate_supersedes_pending_fixes(api):
    book_id, ch = await _pending_fix(api)
    await enqueue(book_id, kind="retranslate", engine="deepseek")
    await pool_for(FakeDeepSeek()).run_until_idle()
    (f,) = await _all_fixes(api, ch)
    assert (f["status"], f["reason"]) == ("rejected", "superseded")
    assert await pending(api, ch.id) == []


async def test_review_job_rechecks_chapter_is_still_reviewable(api):
    from sqlalchemy import update

    book_id, ch = await ct2_translated(api, lines=3)
    r = await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    assert r.status_code == 202
    async with get_sessionmaker()() as s:  # giữa lúc xếp hàng chương được dịch lại bằng DeepSeek
        await s.execute(update(Chapter).where(Chapter.id == ch.id).values(model_id="deepseek-v4-pro"))
        await s.commit()
    fake = FakeDeepSeek()
    await pool_for(fake).run_until_idle()
    assert fake.requests == []  # không gọi API
    async with get_sessionmaker()() as s:
        job = (await s.scalars(select(Job).where(Job.kind == "review"))).one()
    assert job.status == "cancelled" and "không còn soát được" in job.error
    assert await pending(api, ch.id) == []


async def test_review_job_skips_chapter_that_is_busy(api):
    from sqlalchemy import update

    book_id, ch = await ct2_translated(api, lines=3)
    await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    async with get_sessionmaker()() as s:
        await s.execute(update(Chapter).where(Chapter.id == ch.id).values(status="queued"))
        await s.commit()
    fake = FakeDeepSeek()
    await pool_for(fake).run_until_idle()
    assert fake.requests == []


async def test_applied_fix_updates_current_engine_column(api):
    # BR-6.12, Review Focus 2: chương HachimiMT -> fix ghi vào dst_mt, không đụng dst_ai
    book_id, ch = await ct2_translated(api, lines=3)
    segs = {s.idx: s for s in await segments(ch.id)}
    fix = {"idx": 3, "type": "mistranslation", "before": segs[3].dst, "after": "Câu ba đã sửa.", "reason": "sai",
           "confidence": 80}
    await api.post(f"/api/v1/chapters/{ch.id}/ai-review")
    fake = FakeDeepSeek()
    fake.script(fixes_reply(fix))
    await pool_for(fake).run_until_idle()
    (f,) = await pending(api, ch.id)
    assert (await api.post("/api/v1/review-fixes/apply", json={"ids": [f["id"]]})).json()["applied"] == 1
    after = {s.idx: s for s in await segments(ch.id)}
    assert after[3].dst == after[3].dst_machine == after[3].dst_mt == "Câu ba đã sửa." and after[3].dst_ai is None
    assert after[4].dst_mt == segs[4].dst_mt == after[4].dst_machine
    reverted = (await api.patch(f"/api/v1/segments/{ch.id}/3", json={"revert": True})).json()["segment"]
    assert reverted["dst"] == "Câu ba đã sửa." and reverted["edited"] is False  # "Khôi phục bản máy" không đổi nghĩa
