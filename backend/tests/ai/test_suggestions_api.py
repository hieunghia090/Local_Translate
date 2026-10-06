import uuid

import pytest
from sqlalchemy import func, select

from ai.g9_support import LINE, add_suggestion, book_from_texts, put_readings, readings_rows, story, suggestions, term, terms_reply
from ai.support import log_rows, pool_for
from app.db import get_sessionmaker
from app.models import GlossaryTerm
from fake_deepseek import FakeDeepSeek
from helpers import make_book

pytestmark = pytest.mark.db

FIVE = [("赵楷", "Triệu Khải"), ("苏清雪", "Tô Thanh Tuyết"), ("林凡", "Lâm Phàm"), ("高俅", "Cao Cầu"), ("外门", "Ngoại môn")]


async def _accept(api, book_id, items):
    r = await api.post(f"/api/v1/books/{book_id}/glossary/suggestions/accept", json={"items": items})
    assert r.status_code == 200, r.text
    return r.json()


async def test_ac_4_7_accept_five_one_edited(api):
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    ids = [await add_suggestion(book_id, src, dst) for src, dst in FIVE]
    r = await _accept(api, book_id, [{"id": ids[0], "dst_vi": "Triệu Khải Đế"}, *({"id": i} for i in ids[1:])])
    assert (r["added"], r["existing"], r["skipped"], len(r["term_ids"])) == (5, 0, 0, 5)
    terms = {t["src_zh"]: (t["dst_vi"], t["origin"]) for t in (await api.get(f"/api/v1/books/{book_id}/glossary")).json()["items"]}
    assert terms["赵楷"] == ("Triệu Khải Đế", "ai") and all(o == "ai" for _, o in terms.values()) and len(terms) == 5
    assert (await api.get(f"/api/v1/books/{book_id}/glossary/suggestions?status=pending")).json()["items"] == []
    accepted = (await api.get(f"/api/v1/books/{book_id}/glossary/suggestions?status=accepted")).json()["items"]
    assert {s["src_zh"]: s["dst_vi"] for s in accepted}["赵楷"] == "Triệu Khải Đế"


async def test_ac_4_17_accept_learns_missing_char(api):
    await put_readings({"赵": ["triệu"]})
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    sid = await add_suggestion(book_id, "赵楷", "Triệu Khải")
    await _accept(api, book_id, [{"id": sid}])
    assert ("楷", "khải", "learned") in {(r.char, r.reading, r.source) for r in await readings_rows()}


async def test_accept_existing_term_and_reject(api):
    # BR-4.12
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    await api.post(f"/api/v1/books/{book_id}/glossary", json={"src_zh": "林凡", "dst_vi": "Lâm Phàm", "category": "character"})
    a = await add_suggestion(book_id, "林凡", "Lâm Phạm")
    b = await add_suggestion(book_id, "高俅", "Cao Cầu")
    assert (await _accept(api, book_id, [{"id": a}]))["existing"] == 1
    r = await api.post(f"/api/v1/books/{book_id}/glossary/suggestions/reject", json={"ids": [b, str(uuid.uuid4())]})
    assert r.json() == {"rejected": 1}
    assert {s.src_zh: s.status for s in await suggestions(book_id)} == {"林凡": "accepted", "高俅": "rejected"}
    assert (await api.post(f"/api/v1/books/{book_id}/glossary/suggestions/accept", json={"items": []})).status_code == 422
    assert (await api.post(f"/api/v1/books/{book_id}/glossary/suggestions/accept",
                           json={"items": [{"id": a, "dst_vi": "  "}]})).status_code == 422


async def test_terms_added_during_job_and_double_accept(api):
    # Review Focus 4
    book_id, _ = await book_from_texts(api, [story(1, "\n".join([LINE] * 3))])
    await api.post(f"/api/v1/books/{book_id}/glossary/extract", json={"scope": {"mode": "first_n", "n": 1}})

    async def respond(body):
        async with get_sessionmaker()() as s:  # người dùng thêm 苏清雪 trong lúc DeepSeek đang trả lời
            s.add(GlossaryTerm(book_id=uuid.UUID(book_id), src_zh="苏清雪", dst_vi="Tô Thanh Tuyết", category="character"))
            await s.commit()
        return terms_reply(term("赵楷", "Triệu Khải"), term("苏清雪", "Tô Thanh Tuyết"))

    await pool_for(FakeDeepSeek(respond)).run_until_idle()
    rows = await suggestions(book_id)
    assert [s.src_zh for s in rows] == ["赵楷"]
    done = [x for x in await log_rows(source="glossary") if "Trích glossary xong" in x.message]
    assert done[0].detail["late_existing"] == 1
    sid = str(rows[0].id)
    assert (await _accept(api, book_id, [{"id": sid}]))["added"] == 1
    assert await _accept(api, book_id, [{"id": sid}]) == {"added": 0, "existing": 0, "skipped": 1, "term_ids": []}
    async with get_sessionmaker()() as s:
        assert await s.scalar(select(func.count()).select_from(GlossaryTerm).where(GlossaryTerm.src_zh == "赵楷")) == 1


async def test_health_han_in_dst_and_suspicious(api):
    # BR-4.14, BR-4.20
    await put_readings({"赵": ["triệu"], "楷": ["giai"]})
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    for src, dst in (("赵楷", "Triệu Khải"), ("高俅", "高俅 Cầu")):
        await api.post(f"/api/v1/books/{book_id}/glossary", json={"src_zh": src, "dst_vi": dst, "category": "character"})
    h = (await api.get(f"/api/v1/books/{book_id}/glossary/health")).json()
    assert [t["src_zh"] for t in h["han_in_dst"]] == ["高俅"]
    assert [(x["char"], x["proposed"]) for x in h["suspicious_readings"]] == [("楷", "khải")]
    await api.put("/api/v1/hanviet/楷", json={"readings": ["khải", "giai"]})
    assert (await api.get(f"/api/v1/books/{book_id}/glossary/health")).json()["suspicious_readings"] == []


async def test_send_filter_and_summary(api):
    # BR-4.16
    await put_readings({"林": ["lâm"], "凡": ["phàm"], "苏": ["tô"], "雪": ["tuyết"]})
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    for body in ({"src_zh": "林凡", "dst_vi": "Lâm Phàm"}, {"src_zh": "高俅", "dst_vi": "Cao Cầu"},
                 {"src_zh": "苏雪", "dst_vi": "Tô Tuyết", "always_send": True}):
        await api.post(f"/api/v1/books/{book_id}/glossary", json={**body, "category": "character"})
    url = f"/api/v1/books/{book_id}/glossary"
    data = (await api.get(url)).json()
    assert data["summary"] == {"total": 3, "will_send": 2}
    assert {t["src_zh"] for t in (await api.get(url, params={"send": "send"})).json()["items"]} == {"高俅", "苏雪"}
    assert [t["src_zh"] for t in (await api.get(url, params={"send": "predictable"})).json()["items"]] == ["林凡"]


# ---------- phân trang và thao tác hàng loạt theo bộ lọc (review: danh sách lớn) ----------

async def _bulk_suggestions(book_id, n: int, confidence: int = 90) -> None:
    from app.models import GlossarySuggestion, utcnow

    now = utcnow()
    async with get_sessionmaker()() as s:
        s.add_all(GlossarySuggestion(
            book_id=uuid.UUID(str(book_id)), src_zh=chr(0x4E00 + i) + chr(0x4E01 + i), dst_vi=f"Tên {i}", category="character",
            confidence=confidence, occurrence_count=n - i, provider="deepseek", status="pending", created_at=now, updated_at=now)
            for i in range(n))
        await s.commit()


async def test_suggestions_list_is_paginated_with_total(api):
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    await _bulk_suggestions(book_id, 5)
    url = f"/api/v1/books/{book_id}/glossary/suggestions"
    first = (await api.get(url, params={"status": "pending", "limit": 2, "offset": 0})).json()
    assert (len(first["items"]), first["total"], first["limit"], first["offset"]) == (2, 5, 2, 0)
    last = (await api.get(url, params={"status": "pending", "limit": 2, "offset": 4})).json()
    assert len(last["items"]) == 1 and last["total"] == 5
    ids = [i["id"] for p in (0, 2, 4) for i in (await api.get(url, params={"status": "pending", "limit": 2, "offset": p})).json()["items"]]
    assert len(set(ids)) == 5  # thứ tự ổn định, không trùng / sót giữa các trang
    everything = (await api.get(url, params={"status": "pending"})).json()
    assert everything["total"] == 5 and len(everything["items"]) == 5 and everything["limit"] == 200
    assert (await api.get(url, params={"limit": 5000})).status_code == 422


async def test_reject_all_pending_by_filter_beyond_2000(api):
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    await _bulk_suggestions(book_id, 2100)
    r = await api.post(f"/api/v1/books/{book_id}/glossary/suggestions/reject", json={"filter": {}})
    assert r.status_code == 200 and r.json() == {"rejected": 2100}
    assert (await api.get(f"/api/v1/books/{book_id}/glossary/suggestions?status=pending")).json()["total"] == 0


async def test_reject_by_confidence_filter(api):
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    low = await add_suggestion(book_id, "赵楷", "Triệu Khải", conf=60)
    await add_suggestion(book_id, "高俅", "Cao Cầu", conf=95)
    r = await api.post(f"/api/v1/books/{book_id}/glossary/suggestions/reject", json={"filter": {"max_confidence": 79}})
    assert r.json() == {"rejected": 1}
    assert {s.src_zh: s.status for s in await suggestions(book_id)} == {"赵楷": "rejected", "高俅": "pending"}
    assert low


async def test_accept_all_pending_by_filter_beyond_2000(api):
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    await _bulk_suggestions(book_id, 2100)
    r = await api.post(f"/api/v1/books/{book_id}/glossary/suggestions/accept", json={"filter": {"min_confidence": 80}})
    assert r.status_code == 200, r.text
    assert (r.json()["added"], r.json()["existing"]) == (2100, 0)
    assert (await api.get(f"/api/v1/books/{book_id}/glossary/suggestions?status=pending")).json()["total"] == 0


async def test_accept_items_plus_filter_with_exclusions(api):
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    edited = await add_suggestion(book_id, "赵楷", "Triệu Khải", conf=90)
    skipped = await add_suggestion(book_id, "苏清雪", "Tô Thanh Tuyết", conf=95)
    await add_suggestion(book_id, "林凡", "Lâm Phàm", conf=85)
    await add_suggestion(book_id, "高俅", "Cao Cầu", conf=50)
    r = await api.post(f"/api/v1/books/{book_id}/glossary/suggestions/accept", json={
        "items": [{"id": edited, "dst_vi": "Triệu Khải Đế"}], "filter": {"min_confidence": 80, "exclude_ids": [edited, skipped]}})
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 2
    terms = {t["src_zh"]: t["dst_vi"] for t in (await api.get(f"/api/v1/books/{book_id}/glossary")).json()["items"]}
    assert terms == {"赵楷": "Triệu Khải Đế", "林凡": "Lâm Phàm"}
    assert {s.src_zh: s.status for s in await suggestions(book_id)} == {
        "赵楷": "accepted", "苏清雪": "pending", "林凡": "accepted", "高俅": "pending"}
