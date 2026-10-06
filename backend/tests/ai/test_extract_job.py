import json
import uuid

import pytest
from sqlalchemy import select, update

from ai.g9_support import LINE, book_from_texts, jobs, story, suggestions, term, terms_reply
from ai.support import add_term, get, log_rows, pool_for
from app.db import get_sessionmaker
from app.deepseek.extract import EXTRACT_SYSTEM
from app.models import Chapter, GlossarySuggestion, Job, WorkerState
from fake_deepseek import FAKE_KEY, FakeDeepSeek, error, reply, system_of, user_of

pytestmark = pytest.mark.db

TWENTY = [story(i, "\n".join([LINE] * 60)) for i in range(1, 21)]


async def _extract(api, book_id, **body):
    r = await api.post(f"/api/v1/books/{book_id}/glossary/extract", json=body)
    assert r.status_code == 202, r.text
    return r.json()


async def _batch_lines(book_id=None):
    return [x for x in await log_rows(source="glossary") if x.tokens_in is not None]


async def test_ac_4_5_twenty_chapters_pending_and_one_log_per_batch(api):
    book_id, chapters = await book_from_texts(api, TWENTY)
    assert (await _extract(api, book_id, model="deepseek-v4-pro", scope={"mode": "first_n", "n": 20}))["chapters"] == 20
    fake = FakeDeepSeek(lambda body: terms_reply(term("赵楷", "Triệu Khải", conf=95), term("苏清雪", "Tô Thanh Tuyết", conf=70),
                                                 prompt=4000, completion=300, cached=2000))
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 2
    for body in fake.requests:
        assert body["model"] == "deepseek-v4-pro" and body["thinking"] == {"type": "disabled"}
        assert body["response_format"] == {"type": "json_object"} and body["temperature"] == 0.1
    listed = (await api.get(f"/api/v1/books/{book_id}/glossary/suggestions?status=pending")).json()
    by_src = {s["src_zh"]: s for s in listed["items"]}
    assert set(by_src) == {"赵楷", "苏清雪"}
    assert (by_src["赵楷"]["selected"], by_src["苏清雪"]["selected"]) == (True, False)  # BR-4.7
    assert by_src["赵楷"]["occurrence_count"] == 1200 and by_src["赵楷"]["context"]
    assert listed["last_job"]["status"] == "done" and listed["last_job"]["auto"] is False
    lines = await _batch_lines()
    assert len(lines) == 2
    for x in lines:
        assert x.model == "deepseek-v4-pro" and x.provider == "deepseek" and float(x.cost_usd) > 0
        assert (x.detail["usage"]["prompt_tokens"], x.detail["usage"]["completion_tokens"],
                x.detail["usage"]["prompt_cache_hit_tokens"]) == (4000, 300, 2000)
        assert x.tokens_in_est > 0 and x.tokens_out_est > 0
    async with get_sessionmaker()() as s:
        assert all(c.ai_scanned_at is not None for c in (await s.scalars(select(Chapter))).all())
    (job,) = await jobs("ai_extract")
    assert (job.status, job.engine, job.chapter_id, job.tokens_in) == ("done", "deepseek", None, 8000)
    r = await api.post(f"/api/v1/books/{book_id}/glossary/extract", json={"scope": {"mode": "unscanned"}})
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_CHAPTERS_TO_SCAN"  # BR-4.11


async def test_ac_4_13_request_has_no_existing_glossary(api):
    book_id, _ = await book_from_texts(api, [story(1, "\n".join([LINE] * 3))])
    await add_term(book_id, "赵楷", "Triệu Khải")
    await _extract(api, book_id, scope={"mode": "first_n", "n": 1})
    fake = FakeDeepSeek(lambda body: terms_reply(term("赵楷", "Triệu Khải"), term("苏清雪", "Tô Thanh Tuyết")))
    await pool_for(fake).run_until_idle()
    body = fake.requests[0]
    user = user_of(body)
    assert system_of(body) == EXTRACT_SYSTEM
    assert user.startswith("# LOẠI CẦN TRÍCH\ncharacter, organization, realm, location\n\n# VĂN BẢN\n第1章 风云1\n")
    assert "Triệu Khải" not in user and "THUẬT NGỮ" not in user
    (line,) = await _batch_lines()
    assert line.detail["request"]["messages"][1]["content"] == user  # xem được trong log chi tiết
    assert line.detail["dropped"]["dropped_existing"] == 1
    assert [s.src_zh for s in await suggestions(book_id)] == ["苏清雪"]


async def test_ac_4_10_ac_4_11_filters_logged(api):
    book_id, _ = await book_from_texts(api, [story(1, "林凡来了。林凡走了。\n" + LINE + "\n" + LINE)])
    await _extract(api, book_id, scope={"mode": "first_n", "n": 1})
    fake = FakeDeepSeek(lambda body: terms_reply(
        term("赵楷", "赵楷"),
        {"type": "person", "source_term": "苏清雪", "suggested_target": "Tô Thanh Tuyết", "confidence": 90},
        term("林凡", "Lâm Phàm")))
    await pool_for(fake).run_until_idle()
    (line,) = await _batch_lines()
    assert line.detail["dropped"] == {"dropped_invalid": 1, "dropped_existing": 0, "dropped_han_target": 1, "dropped_low_freq": 0}
    assert [s.src_zh for s in await suggestions(book_id)] == ["林凡"]


async def test_ac_4_12_truncated_output_keeps_37(api):
    names = [chr(0x5100 + 2 * i) + chr(0x5101 + 2 * i) for i in range(40)]
    book_id, _ = await book_from_texts(api, [story(1, "。".join(n * 2 for n in names))])
    await _extract(api, book_id, scope={"mode": "first_n", "n": 1})
    items = [term(n, f"Tên {i}") for i, n in enumerate(names)]
    full = json.dumps({"terms": items}, ensure_ascii=False)
    cut = full.index(json.dumps(items[37], ensure_ascii=False)) + 20
    fake = FakeDeepSeek()
    fake.script(reply(full[:cut], prompt=3000, completion=900))
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 1  # cứu được thì không thử lại
    assert len(await suggestions(book_id)) == 37
    (line,) = await _batch_lines()
    assert line.level == "warn" and "output bị cắt, cứu được 37 mục" in line.message


async def test_ac_4_9_retry_then_failed_keeps_successful_batches(api):
    book_id, chapters = await book_from_texts(api, TWENTY)
    await _extract(api, book_id, scope={"mode": "first_n", "n": 20})
    fake = FakeDeepSeek()
    fake.script(terms_reply(term("赵楷", "Triệu Khải")), error(429, headers={"Retry-After": "1"}), error(500), error(502),
                error(503))
    await pool_for(fake).run_until_idle()
    assert fake.sleeps == [1.0, 2.0, 4.0]
    (job,) = await jobs("ai_extract")
    assert job.status == "failed" and "HTTP 503" in job.error
    assert [s.src_zh for s in await suggestions(book_id)] == ["赵楷"]
    (err,) = await log_rows(level="error", source="glossary")
    assert "lô 2/2" in err.message and "Giữ 1 đề xuất" in err.message
    assert len([x for x in await log_rows(level="warn", source="glossary")]) == 3  # 429 + 2 lần thử lại
    async with get_sessionmaker()() as s:
        scanned = {c.no for c in (await s.scalars(select(Chapter).where(Chapter.ai_scanned_at.is_not(None)))).all()}
    assert scanned == set(range(1, 13))  # chương 13 nằm cả ở lô 2 nên chưa quét xong
    assert (await _extract(api, book_id, scope={"mode": "unscanned"}))["chapters"] == 8


async def test_ac_4_6_bad_key_pauses_without_suggestions_or_key_in_logs(api, fake_deepseek):
    fake_deepseek.script(error(401, f"Authentication Fails {FAKE_KEY}"))
    r = (await api.post("/api/v1/deepseek/test")).json()
    assert (r["ok"], r["status"]) == (False, 401) and "401" in r["message"] and FAKE_KEY not in r["message"]
    book_id, _ = await book_from_texts(api, [story(1, LINE + "\n" + LINE)])
    await _extract(api, book_id, scope={"mode": "first_n", "n": 1})
    fake = FakeDeepSeek(lambda body: error(401, f"Authentication Fails {FAKE_KEY}"))
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 1 and await suggestions(book_id) == []
    (job,) = await jobs("ai_extract")
    assert job.status == "paused"
    assert (await get(WorkerState, "deepseek")).paused_reason == "auth"
    dump = json.dumps([[x.message, x.detail, x.params] for x in await log_rows()], ensure_ascii=False, default=str)
    assert FAKE_KEY not in dump


async def test_rerun_updates_pending_and_never_reproposes_decided(api):
    # Review Focus 1
    book_id, _ = await book_from_texts(api, [story(1, "林凡。林凡。\n" + LINE + "\n" + LINE)])
    first = lambda body: terms_reply(term("赵楷", "Triệu Khải"), term("苏清雪", "Tô Thanh Tuyết"), term("林凡", "Lâm Phàm"))
    await _extract(api, book_id, scope={"mode": "first_n", "n": 1})
    await pool_for(FakeDeepSeek(first)).run_until_idle()
    async with get_sessionmaker()() as s:
        await s.execute(update(GlossarySuggestion).where(GlossarySuggestion.src_zh == "苏清雪").values(status="rejected"))
        await s.execute(update(GlossarySuggestion).where(GlossarySuggestion.src_zh == "林凡").values(status="accepted"))
        await s.commit()
    second = lambda body: terms_reply(term("赵楷", "Triệu Giai", conf=60), term("苏清雪", "Tô Thanh Tuyết"), term("林凡", "Lâm Phàm"))
    await _extract(api, book_id, scope={"mode": "range", "from": 1, "to": 1})
    await pool_for(FakeDeepSeek(second)).run_until_idle()
    rows = {s.src_zh: (s.dst_vi, s.status, s.confidence) for s in await suggestions(book_id)}
    assert rows == {"赵楷": ("Triệu Giai", "pending", 60), "苏清雪": ("Tô Thanh Tuyết", "rejected", 95),
                    "林凡": ("Lâm Phàm", "accepted", 95)}
    assert (await _batch_lines())[-1].detail["dropped"]["dropped_existing"] == 2


async def test_scope_validation_busy_and_recovery(api):
    book_id, _ = await book_from_texts(api, [story(1, LINE + "\n" + LINE)])
    r = await api.post(f"/api/v1/books/{book_id}/glossary/extract", json={"scope": {"mode": "range", "from": 5, "to": 2}})
    assert r.status_code == 422
    r = await api.post(f"/api/v1/books/{book_id}/glossary/extract", json={"categories": ["person"]})
    assert r.status_code == 422
    job_id = (await _extract(api, book_id, scope={"mode": "first_n", "n": 1}))["job_id"]
    r = await api.post(f"/api/v1/books/{book_id}/glossary/extract", json={"scope": {"mode": "first_n", "n": 1}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "EXTRACT_BUSY"
    async with get_sessionmaker()() as s:
        await s.execute(update(Job).where(Job.id == uuid.UUID(job_id)).values(status="running"))
        await s.commit()
    await pool_for(FakeDeepSeek()).startup()  # NFR-3: job trích bị ngắt quay lại hàng đợi
    assert (await get(Job, uuid.UUID(job_id))).status == "queued"


async def test_estimate_does_not_create_job(api):
    book_id, _ = await book_from_texts(api, TWENTY)
    e = (await api.post(f"/api/v1/books/{book_id}/glossary/extract/estimate",
                        json={"model": "deepseek-flash", "scope": {"mode": "unscanned"}})).json()
    assert (e["model_id"], e["chapters"], e["batches"]) == ("deepseek-flash", 20, 2)
    assert e["tokens_in"] > e["tokens_in_cached"] > 0 and e["tokens_out"] > 0 and e["cost_usd"] > 0
    assert await jobs() == []
