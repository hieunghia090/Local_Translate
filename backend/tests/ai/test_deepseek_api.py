import uuid

import pytest
from sqlalchemy import select, update

from app.config import get_settings
from app.db import get_sessionmaker
from app.deepseek.prompts import DEFAULT_FOUNDATION
from app.models import AiModel, Chapter, Job
from app.services import logs, queue
from fake_deepseek import FakeDeepSeek, error, reply
from helpers import make_book

pytestmark = pytest.mark.db


async def _jobs() -> list[Job]:
    async with get_sessionmaker()() as s:
        return list((await s.scalars(select(Job).order_by(Job.position))).all())


async def test_estimate_fifty_chapters_before_queueing(api):
    # AC-8.8 (phần backend), BR-8.24, BR-8.26
    book_id, chapters = await make_book(api, n_chapters=50, lines=2)
    r = await api.post(f"/api/v1/books/{book_id}/estimate",
                       json={"action": "translate", "chapter_ids": [str(c.id) for c in chapters]})
    assert r.status_code == 200, r.text
    e = r.json()
    assert (e["chapters"], e["skipped"], e["model_id"]) == (50, 0, "deepseek-v4-pro")
    assert e["tokens_in"] > 0 and e["tokens_out"] > 0 and e["cost_usd"] > 0 and e["tokens_in_cached"] > 0
    assert e["prices_are_samples"] is True
    assert e["coefficients"] == {"han_per_token": 1.8, "out_ratio": 3.3, "samples": 0, "calibrated": False}
    assert await _jobs() == []  # ước tính không tạo job
    by_filter = (await api.post(f"/api/v1/books/{book_id}/estimate",
                                json={"action": "translate", "filter": {"status": ["todo"]}})).json()
    assert by_filter["tokens_in"] == e["tokens_in"]
    r = await api.post(f"/api/v1/books/{book_id}/estimate", json={"action": "translate"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "ESTIMATE_TARGET_REQUIRED"


async def test_review_estimate_only_counts_hachimi_chapters(api):
    book_id, chapters = await make_book(api, n_chapters=2, lines=1)
    async with get_sessionmaker()() as s:
        await s.execute(update(Chapter).where(Chapter.id == chapters[0].id).values(status="translated", model_id="HachimiMT-60"))
        await s.commit()
    e = (await api.post(f"/api/v1/books/{book_id}/estimate",
                        json={"action": "review", "chapter_ids": [str(c.id) for c in chapters]})).json()
    assert (e["chapters"], e["skipped"], e["model_id"]) == (1, 1, "deepseek-flash")


async def test_calibrated_coefficients_after_fifty_requests(api):
    # AC-8.18, BR-8.24b, BR-8.24c
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    async with get_sessionmaker()() as s:
        for _ in range(50):
            await logs.write_log(s, level="info", source="translate", book_id=uuid.UUID(book_id), message="x",
                                 provider="deepseek", model="deepseek-v4-pro", tokens_in=500, tokens_out=400,
                                 tokens_in_est=400, tokens_out_est=300, cost_usd=0.001,
                                 params={"partial": True, "estimate": {"text_tokens": 200, "overhead_tokens": 100}})
        await s.commit()
    usage = (await api.get(f"/api/v1/books/{book_id}/usage")).json()
    (row,) = usage["items"]
    assert (row["model"], row["source"], row["requests"], row["tokens_in"]) == ("deepseek-v4-pro", "translate", 50, 25000)
    assert usage["total"]["cost_usd"] == 0.05
    acc = next(a for a in usage["accuracy"] if a["model"] == "deepseek-v4-pro")
    assert (acc["requests"], acc["err_in_pct"], acc["err_out_pct"]) == (50, 20.0, 25.0)
    assert acc["coefficients"] == {"han_per_token": 0.9, "out_ratio": 1.0, "samples": 50, "calibrated": True}
    e = (await api.post(f"/api/v1/books/{book_id}/estimate",
                        json={"action": "translate", "chapter_ids": [str(chapters[0].id)]})).json()
    assert e["coefficients"]["han_per_token"] == 0.9 and e["coefficients"]["han_per_token"] != 1.8
    assert (await api.get(f"/api/v1/books/{book_id}/usage?month=2000-01")).json()["items"] == []
    assert (await api.get(f"/api/v1/books/{book_id}/usage?month=2026-13")).status_code == 422


async def test_bulk_translate_with_deepseek_engine_keeps_book_config(api):
    book_id, chapters = await make_book(api, n_chapters=2, lines=1)
    r = await api.post(f"/api/v1/books/{book_id}/chapters/bulk",
                       json={"action": "translate", "chapter_ids": [str(c.id) for c in chapters], "engine": "deepseek"})
    assert r.json()["affected"] == 2
    jobs = await _jobs()
    assert {(j.engine, j.run_config["engine"], j.run_config["model_id"]) for j in jobs} == {("deepseek", "deepseek", "deepseek-v4-pro")}
    book = (await api.get(f"/api/v1/books/{book_id}")).json()
    assert book["run_config"]["engine"] == "ct2"


async def test_bulk_review_only_hachimi_chapters(api):
    # BR-3.2a, mục 5.3
    book_id, chapters = await make_book(api, n_chapters=3, lines=1)
    async with get_sessionmaker()() as s:
        await s.execute(update(Chapter).where(Chapter.id == chapters[0].id).values(status="translated", model_id="HachimiMT-60"))
        await s.execute(update(Chapter).where(Chapter.id == chapters[1].id).values(status="translated", model_id="deepseek-v4-pro"))
        await s.commit()
    r = (await api.post(f"/api/v1/books/{book_id}/chapters/bulk",
                        json={"action": "review", "chapter_ids": [str(c.id) for c in chapters]})).json()
    assert (r["affected"], r["skipped"]) == (1, 2)
    (job,) = await _jobs()
    assert (job.kind, job.engine, job.chapter_id) == ("review", "deepseek", chapters[0].id)


async def test_translate_chapter_with_engine_override(api):
    # BR-6.5a
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    r = await api.post(f"/api/v1/chapters/{chapters[0].id}/translate", json={"engine": "deepseek"})
    assert r.status_code == 202
    (job,) = await _jobs()
    assert (job.engine, job.run_config["model_id"]) == ("deepseek", "deepseek-v4-pro")


async def test_foundation_get_put_reset_and_preview(api):
    # US-8.2, BR-8.1
    book_id, chapters = await make_book(api, n_chapters=2, lines=1)
    f = (await api.get(f"/api/v1/books/{book_id}/foundation")).json()
    assert f["is_default"] and f["foundation_prompt"] == DEFAULT_FOUNDATION and f["honorific_block"].startswith("# XƯNG HÔ")
    f = (await api.put(f"/api/v1/books/{book_id}/foundation", json={"foundation_prompt": "  Giọng văn hài hước.  "})).json()
    assert (f["is_default"], f["foundation_prompt"]) == (False, "Giọng văn hài hước.")
    p1 = (await api.get(f"/api/v1/books/{book_id}/foundation/preview?chapter_no=1")).json()
    p2 = (await api.get(f"/api/v1/books/{book_id}/foundation/preview?chapter_no=2")).json()
    assert p1["system"] == p2["system"] and p1["system"].startswith("Giọng văn hài hước.\n\n# XƯNG HÔ")
    assert "⟦0⟧ 第1章 标题1" in p1["user"] and "# THUẬT NGỮ" not in p1["user"] and p1["tokens_in_est"] > 0
    assert (await api.post(f"/api/v1/books/{book_id}/foundation/reset")).json()["is_default"] is True
    assert (await api.get(f"/api/v1/books/{book_id}/foundation/preview?chapter_no=99")).status_code == 404
    assert (await api.put(f"/api/v1/books/{book_id}/foundation", json={"foundation_prompt": ""})).status_code == 422


async def test_notes_crud(api):
    # US-8.7
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    cid = chapters[0].id
    r = await api.post(f"/api/v1/chapters/{cid}/notes", json={"content": "  Tên 高俅 phải là Cao Cầu "})
    assert r.status_code == 201
    note = r.json()
    assert (note["type"], note["content"], note["resolved"]) == ("correction", "Tên 高俅 phải là Cao Cầu", False)
    assert [n["id"] for n in (await api.get(f"/api/v1/chapters/{cid}/notes")).json()["items"]] == [note["id"]]
    assert (await api.patch(f"/api/v1/notes/{note['id']}", json={"resolved": True})).json()["resolved"] is True
    assert (await api.delete(f"/api/v1/notes/{note['id']}")).status_code == 204
    assert (await api.get(f"/api/v1/chapters/{cid}/notes")).json()["items"] == []
    assert (await api.post(f"/api/v1/chapters/{cid}/notes", json={"content": "   "})).status_code == 422
    assert (await api.patch(f"/api/v1/notes/{uuid.uuid4()}", json={"resolved": True})).status_code == 404


async def test_ai_models_patch_price_clears_sample_flag(api):
    # BR-8.25
    items = (await api.get("/api/v1/ai-models")).json()["items"]
    assert [m["id"] for m in items] == ["deepseek-flash", "deepseek-v4-pro"] and all(m["prices_are_samples"] for m in items)
    m = (await api.patch("/api/v1/ai-models/deepseek-flash", json={"price_out_per_mtok": 0.5})).json()
    assert (m["price_out_per_mtok"], m["prices_are_samples"]) == (0.5, False)
    m = (await api.patch("/api/v1/ai-models/deepseek-v4-pro", json={"enabled": False})).json()
    assert m["prices_are_samples"] is True and m["enabled"] is False
    assert (await api.patch("/api/v1/ai-models/khong-co", json={"enabled": True})).status_code == 404
    assert (await api.patch("/api/v1/ai-models/deepseek-flash", json={"price_in_per_mtok": -1})).status_code == 422


async def test_status_reports_key_and_pause_reason(api):
    # BR-8.13 banner, 00 mục 9
    st = (await api.get("/api/v1/deepseek/status")).json()
    key = get_settings().deepseek_api_key.strip()
    assert st["key_present"] == bool(key) and (st["key_masked"] or "").endswith(key[-4:])
    assert (st["paused"], st["paused_reason"], st["message"]) == (False, None, None)
    async with get_sessionmaker()() as s:
        await queue.pause_engine(s, "deepseek", reason="auth", park_queued=True)
        await s.commit()
    st = (await api.get("/api/v1/deepseek/status")).json()
    assert (st["paused"], st["paused_reason"]) == (True, "auth")
    assert st["message"] == "DeepSeek từ chối key / hết số dư. Kiểm tra trong Cài đặt."
    if key:
        assert key not in str(st)


async def test_connection_test_uses_injected_client(api, fake_deepseek: FakeDeepSeek):
    fake_deepseek.script(reply("OK", prompt=10, completion=1))
    r = (await api.post("/api/v1/deepseek/test")).json()
    assert r["ok"] is True and r["reasoning_tokens"] == 0
    assert fake_deepseek.requests[0]["thinking"] == {"type": "disabled"}
    fake_deepseek.script(error(401))
    r = (await api.post("/api/v1/deepseek/test")).json()
    assert (r["ok"], r["status"]) == (False, 401)


async def test_estimate_counts_system_and_glossary_once_per_part(api):
    # BR-8.24: phần cố định (system + glossary) tính mỗi request, tức mỗi phần; số request khớp pool thật
    from ai.support import ds_book, pool_for
    from helpers import enqueue

    book_id, chapters = await ds_book(api, lines=60)
    async with get_sessionmaker()() as s:
        await s.execute(update(AiModel).where(AiModel.id == "deepseek-v4-pro").values(context_window=600, max_output_tokens=256))
        await s.commit()
    ids = [str(chapters[0].id)]
    e = (await api.post(f"/api/v1/books/{book_id}/estimate", json={"action": "translate", "chapter_ids": ids})).json()
    await enqueue(book_id)
    fake = FakeDeepSeek()
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) >= 2
    assert e["requests"] == len(fake.requests)
    async with get_sessionmaker()() as s:
        await s.execute(update(AiModel).where(AiModel.id == "deepseek-v4-pro").values(context_window=1_000_000, max_output_tokens=384_000))
        await s.commit()
    one = (await api.post(f"/api/v1/books/{book_id}/estimate", json={"action": "translate", "chapter_ids": ids})).json()
    assert one["requests"] == 1 and e["tokens_in"] > one["tokens_in"]
    assert e["tokens_in_cached"] == (e["requests"] - 1) * (one["tokens_in_cached"] or 0) or e["tokens_in_cached"] > 0


async def test_connection_test_waits_at_most_one_429(api, fake_deepseek: FakeDeepSeek):
    fake_deepseek.script(error(429, "quá tải", {"retry-after": "2"}), error(429, "quá tải", {"retry-after": "2"}), reply("không tới"))
    r = (await api.post("/api/v1/deepseek/test")).json()
    assert (r["ok"], r["status"]) == (False, 429)
    assert len(fake_deepseek.requests) == 2 and fake_deepseek.sleeps == [2.0]  # chờ đúng 1 lần


async def test_connection_test_does_not_sleep_for_long_retry_after(api, fake_deepseek: FakeDeepSeek):
    fake_deepseek.script(error(429, "quá tải", {"retry-after": "120"}), reply("không tới"))
    r = (await api.post("/api/v1/deepseek/test")).json()
    assert r["ok"] is False and len(fake_deepseek.requests) == 1 and fake_deepseek.sleeps == []
