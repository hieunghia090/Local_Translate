import uuid

import httpx
import pytest
from sqlalchemy import select

from ai.g9_support import LINE, import_from_texts, is_extract, jobs, story, term, terms_reply
from ai.support import log_rows
from app.db import get_sessionmaker
from app.deepseek.client import DeepSeekClient
from app.main import create_app
from app.models import Chapter, GlossaryTerm
from app.services import extraction
from fake_deepseek import user_of
from helpers import wait_ready

pytestmark = pytest.mark.db

TEXTS = [story(i, f"第{i}章正文。\n" + LINE + "\n" + LINE) for i in range(1, 26)]


def responder(body):
    return terms_reply(term("赵楷", "Triệu Khải", conf=95), term("苏清雪", "Tô Thanh Tuyết", conf=70))


async def _preview(api, import_id, **body):
    return await api.post(f"/api/v1/imports/{import_id}/glossary-preview", json={"chapters": 5, **body})


async def test_preview_runs_sync_on_first_n_kept_chapters(api, fake_deepseek):
    view = await import_from_texts(api, TEXTS)
    fake_deepseek.responder = responder
    r = await _preview(api, view["import_id"])
    assert r.status_code == 200, r.text
    data = r.json()
    items = {i["src_zh"]: i for i in data["items"]}
    assert (items["赵楷"]["selected"], items["苏清雪"]["selected"]) == (True, False)
    assert items["赵楷"]["occurrence_count"] == 10 and data["chapters"] == 5 and data["batches"] == 1
    user = user_of(fake_deepseek.requests[0])
    assert "第5章正文" in user and "第6章正文" not in user
    assert fake_deepseek.requests[0]["thinking"] == {"type": "disabled"}
    (line,) = [x for x in await log_rows(source="glossary") if x.tokens_in is not None]
    assert line.book_id is None


async def test_create_with_accepted_preview_and_extract_job(api, fake_deepseek):
    # spec 02 mục 7, AC-2.8
    view = await import_from_texts(api, TEXTS)
    fake_deepseek.responder = responder
    items = {i["src_zh"]: i for i in (await _preview(api, view["import_id"])).json()["items"]}
    zid = items["赵楷"]["id"]
    r = await api.post("/api/v1/books", json={
        "title_zh": "书", "import_id": view["import_id"], "confirm_duplicate": True,
        "ai_extract": {"enabled": True, "model": "deepseek-flash", "chapters": 5,
                       "categories": ["character", "organization"], "accepted_preview_ids": [zid],
                       "preview_edits": {zid: {"dst_vi": "Triệu Khải Đế"}}}})
    assert r.status_code == 201, r.text
    book_id = uuid.UUID(r.json()["id"])
    async with get_sessionmaker()() as s:
        terms = (await s.scalars(select(GlossaryTerm).where(GlossaryTerm.book_id == book_id))).all()
        chapters = (await s.scalars(select(Chapter).where(Chapter.book_id == book_id).order_by(Chapter.no))).all()
    assert [(t.src_zh, t.dst_vi, t.origin) for t in terms] == [("赵楷", "Triệu Khải Đế", "ai")]
    assert {c.status for c in chapters} == {"todo"}
    (job,) = await jobs()
    assert (job.kind, job.engine, job.status) == ("ai_extract", "deepseek", "queued")
    assert job.options["chapter_ids"] == [str(c.id) for c in chapters[:5]]
    assert (job.options["model"], job.options["categories"]) == ("deepseek-flash", ["character", "organization"])


async def test_ai_disabled_creates_no_job_and_old_codes_rejected(api):
    view = await import_from_texts(api, TEXTS[:2])
    r = await api.post("/api/v1/books", json={"title_zh": "书", "import_id": view["import_id"], "confirm_duplicate": True,
                                              "ai_extract": {"enabled": True, "categories": ["person"]}})
    assert r.status_code == 422
    r = await api.post("/api/v1/books", json={"title_zh": "书", "import_id": view["import_id"], "confirm_duplicate": True,
                                              "ai_extract": {"enabled": False}})
    assert r.status_code == 201 and await jobs() == []


async def test_disabled_ai_with_no_categories_is_ok_enabled_needs_categories(api):
    # Review: tắt AI và bỏ hết loại không được trả 422
    view = await import_from_texts(api, TEXTS[:2])
    r = await api.post("/api/v1/books", json={"title_zh": "书", "import_id": view["import_id"], "confirm_duplicate": True,
                                              "ai_extract": {"enabled": True, "categories": []}})
    assert r.status_code == 422
    r = await api.post("/api/v1/books", json={"title_zh": "书", "import_id": view["import_id"], "confirm_duplicate": True,
                                              "ai_extract": {"enabled": False, "categories": []}})
    assert r.status_code == 201, r.text
    assert await jobs() == []


async def test_stale_preview_ids_ignored_after_reparse(api, fake_deepseek):
    # Review Focus 5
    view = await import_from_texts(api, TEXTS)
    fake_deepseek.responder = responder
    zid = (await _preview(api, view["import_id"])).json()["items"][0]["id"]
    r = await api.patch(f"/api/v1/imports/{view['import_id']}", json={"encoding": "utf-8"})  # phân tích lại
    assert r.status_code == 200
    fresh = await wait_ready(api, view["import_id"])
    assert fresh["status"] == "ready"
    await api.patch(f"/api/v1/imports/{view['import_id']}",
                    json={"chapters": [{"key": c["key"], "selected": True} for c in fresh["chapters"]]})
    r = await api.post("/api/v1/books", json={"title_zh": "书", "import_id": view["import_id"], "confirm_duplicate": True,
                                              "ai_extract": {"enabled": False, "accepted_preview_ids": [zid]}})
    assert r.status_code == 201, r.text
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(GlossaryTerm))).all() == []
    assert any("Bỏ 1 đề xuất chạy thử" in x.message for x in await log_rows(level="warn"))


async def test_preview_requires_selected_chapters_and_times_out(api, fake_deepseek, monkeypatch):
    # Review Focus 5
    view = await import_from_texts(api, TEXTS[:5])
    await api.patch(f"/api/v1/imports/{view['import_id']}",
                    json={"chapters": [{"key": c["key"], "selected": False} for c in view["chapters"]]})
    r = await _preview(api, view["import_id"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_CHAPTERS_SELECTED"
    await api.patch(f"/api/v1/imports/{view['import_id']}",
                    json={"chapters": [{"key": c["key"], "selected": True} for c in view["chapters"]]})
    monkeypatch.setattr(extraction, "PREVIEW_TIMEOUT", 0.05)
    fake_deepseek.delay = 0.5
    fake_deepseek.responder = responder
    r = await _preview(api, view["import_id"])
    assert r.status_code == 504 and r.json()["error"]["code"] == "AI_PREVIEW_TIMEOUT"


async def test_preview_without_key(clean_db, data_dir, fake_translator):
    app = create_app(translator_factory=lambda: fake_translator, hanviet_autofill=False,
                     deepseek_client_factory=lambda: DeepSeekClient(api_key="", base_url="https://x.test"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        view = await import_from_texts(client, TEXTS[:5])
        r = await _preview(client, view["import_id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "DEEPSEEK_NO_KEY"


async def test_preview_estimate_matches_scope_and_calls_no_api(api, fake_deepseek):
    # Review: create-book hiện cost trước khi chạy thử / tạo job
    view = await import_from_texts(api, TEXTS)
    r = await api.post(f"/api/v1/imports/{view['import_id']}/glossary-preview/estimate",
                       json={"chapters": 5, "categories": ["character"]})
    assert r.status_code == 200, r.text
    est = r.json()
    assert est["chapters"] == 5 and est["batches"] == 1 and est["tokens_in"] > 0 and est["tokens_out"] > 0
    assert est["cost_usd"] > 0 and est["model_id"] == "deepseek-v4-pro" and "prices_are_samples" in est
    bigger = (await api.post(f"/api/v1/imports/{view['import_id']}/glossary-preview/estimate",
                             json={"chapters": 20, "categories": ["character"]})).json()
    assert bigger["chapters"] == 20 and bigger["tokens_in"] > est["tokens_in"]
    assert fake_deepseek.requests == []
    assert (await api.post("/api/v1/imports/00000000-0000-0000-0000-000000000000/glossary-preview/estimate",
                           json={"chapters": 5})).status_code == 404


async def test_preview_refused_while_deepseek_engine_paused(api, fake_deepseek):
    # Review: engine tạm dừng (auth / token bất thường / lỗi liên tiếp) thì không gọi API trả phí
    from sqlalchemy import text

    view = await import_from_texts(api, TEXTS)
    async with get_sessionmaker()() as s:
        await s.execute(text("UPDATE worker_state SET paused = true, paused_reason = 'token_anomaly' WHERE engine = 'deepseek'"))
        await s.commit()
    fake_deepseek.responder = responder
    r = await _preview(api, view["import_id"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "ENGINE_PAUSED"
    assert "tạm dừng" in r.json()["error"]["message"]
    assert fake_deepseek.requests == []
