import httpx
import pytest
from sqlalchemy import select

from ai.g9_support import asked_chars, hanviet_responder, mark_seed_loaded, put_readings, readings_rows
from ai.support import log_rows
from app.db import get_sessionmaker
from app.main import create_app
from app.models import HanvietUnknown
from app.services import hanviet
from helpers import make_book, upload

pytestmark = pytest.mark.db

TWELVE = "赵楷雪藏苏清林凡高俅外门"


async def _term(api, book_id, src, dst, **kw) -> dict:
    r = await api.post(f"/api/v1/books/{book_id}/glossary", json={"src_zh": src, "dst_vi": dst, "category": "character", **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def test_ac_4_14_predictable_follows_dst(api):
    await put_readings({"林": ["lâm"], "凡": ["phàm"]})
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    t = await _term(api, book_id, "林凡", "Lâm Phàm")
    assert t["predictable"] is True and t["miss_count"] == 0 and t["book_id"] == book_id
    r = (await api.patch(f"/api/v1/glossary/{t['id']}", json={"dst_vi": "Lâm Phàn"})).json()
    assert r["term"]["predictable"] is False
    r = (await api.patch(f"/api/v1/glossary/{t['id']}", json={"dst_vi": "lâm  phàm"})).json()
    assert r["term"]["predictable"] is True


async def test_ac_4_15_foreign_never_predictable(api):
    await put_readings({"赫": ["hách"], "鲁": ["lỗ"], "晓": ["hiểu"], "夫": ["phu"]})
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    assert (await _term(api, book_id, "赫鲁晓夫", "Khrushchev", name_lang="foreign"))["predictable"] is False
    t = await _term(api, book_id, "鲁晓夫", "Lỗ Hiểu Phu", name_lang="foreign")
    assert t["predictable"] is False


async def test_create_learns_missing_char_after_computing_predictable(api):
    # Mục 6a bước 4: 楷 chưa có âm nào → học "khải"; predictable tính trước khi học nên vẫn false
    await put_readings({"赵": ["triệu"]})
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    t = await _term(api, book_id, "赵楷", "Triệu Khải")
    assert t["predictable"] is False
    assert {(r.char, r.reading, r.source) for r in await readings_rows()} == {("赵", "triệu", "ai"), ("楷", "khải", "learned")}


async def test_suspicious_reading_then_manual_fix(api):
    # BR-4.20: 楷 chỉ có âm ai "giai" mà term đã duyệt đọc "khải" → nghi ngờ, không tự học
    await put_readings({"赵": ["triệu"], "楷": ["giai"]})
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    t = await _term(api, book_id, "赵楷", "Triệu Khải")
    assert t["predictable"] is False
    async with get_sessionmaker()() as s:
        sus = await hanviet.suspicious_readings(s, book_id)
    assert sus == [{"char": "楷", "readings": ["giai"], "proposed": "khải",
                    "terms": [{"id": t["id"], "src_zh": "赵楷", "dst_vi": "Triệu Khải"}]}]
    r = await api.put("/api/v1/hanviet/楷", json={"readings": ["Khải", "giai"]})
    assert r.status_code == 200, r.text
    assert {x["reading"]: x["source"] for x in r.json()["readings"]} == {"khải": "manual", "giai": "manual"}
    terms = (await api.get(f"/api/v1/books/{book_id}/glossary")).json()["items"]
    assert terms[0]["predictable"] is True
    async with get_sessionmaker()() as s:
        assert await hanviet.suspicious_readings(s, book_id) == []


async def test_lookup_and_validation(api):
    await put_readings({"赵": ["triệu"]})
    items = (await api.get("/api/v1/hanviet", params={"chars": "赵x楷赵"})).json()["items"]
    assert items == [{"char": "赵", "readings": [{"reading": "triệu", "source": "ai", "confidence": 60}]},
                     {"char": "楷", "readings": []}]
    assert (await api.put("/api/v1/hanviet/ab", json={"readings": ["a"]})).json()["error"]["code"] == "INVALID_CHAR"
    r = await api.put("/api/v1/hanviet/赵", json={"readings": ["tô tô"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_READING"


async def test_import_and_copy_recompute_predictable(api):
    await put_readings({"林": ["lâm"], "凡": ["phàm"]})
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    tsv = "src_zh\tdst_vi\tcategory\n林凡\tLâm Phàm\tcharacter\n".encode()
    r = await api.post(f"/api/v1/books/{book_id}/glossary/import", data={"on_conflict": "keep"},
                       files={"file": ("g.tsv", tsv, "text/tab-separated-values")})
    assert r.status_code == 200, r.text
    assert (await api.get(f"/api/v1/books/{book_id}/glossary")).json()["items"][0]["predictable"] is True
    other, _ = await make_book(api, n_chapters=1, lines=1, title="书二")
    await api.post(f"/api/v1/books/{other}/glossary/copy", json={"from_book_id": book_id})
    assert (await api.get(f"/api/v1/books/{other}/glossary")).json()["items"][0]["predictable"] is True


async def test_ensure_seed_loads_once(clean_db, tmp_path):
    seed = tmp_path / "seed.tsv"
    seed.write_text("char\treading\tsource\tconfidence\n赵\ttriệu\tai\t60\n门\tmôn\tconfirmed\t90\n"
                    "藏\ttàng\tconfirmed\t90\nhỏng\tdòng\nX\tx\tai\t60\n", encoding="utf-8")
    async with get_sessionmaker()() as s:
        assert await hanviet.ensure_seed(s, seed) == 3
        await s.commit()
        assert await hanviet.ensure_seed(s, seed) == 0
        assert await hanviet.ensure_seed(s, tmp_path / "khong-co.tsv") == 0
    assert {(r.char, r.source, r.confidence) for r in await readings_rows()} == {("赵", "ai", 60), ("门", "confirmed", 90), ("藏", "confirmed", 90)}


@pytest.fixture
async def api_fill(clean_db, data_dir, fake_translator, fake_deepseek):
    app = create_app(translator_factory=lambda: fake_translator, deepseek_client_factory=fake_deepseek.client,
                     hanviet_autofill=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _create(api_fill, text: str, title: str, *, seeded: bool = True) -> None:
    if seeded:
        await mark_seed_loaded()
    view = await upload(api_fill, [("0001.txt", text.encode())])
    await api_fill.patch(f"/api/v1/imports/{view['import_id']}",
                         json={"chapters": [{"key": c["key"], "selected": True} for c in view["chapters"]]})
    r = await api_fill.post("/api/v1/books", json={"title_zh": title, "import_id": view["import_id"], "confirm_duplicate": True})
    assert r.status_code == 201, r.text


async def test_ac_4_18_background_fill_after_create(api_fill, fake_deepseek):
    table = {c: [r] for c, r in zip(TWELVE, ["triệu", "khải", "tuyết", "tàng", "tô", "thanh", "lâm", "phàm", "cao",
                                              "cầu", "ngoại", "môn"])}
    fake_deepseek.responder = hanviet_responder(table)
    await _create(api_fill, "赵楷雪藏\n苏清林凡\n高俅外门\n", "书")
    assert len(fake_deepseek.requests) == 1 and sorted(asked_chars(fake_deepseek.requests[0])) == sorted(TWELVE)
    rows = await readings_rows()
    assert {r.char for r in rows} == set(TWELVE) and {r.source for r in rows} == {"ai"}
    (line,) = [x for x in await log_rows(source="glossary") if "âm Hán Việt" in x.message]
    assert "12 chữ" in line.message and line.tokens_in == 200 and line.tokens_out == 120 and line.cost_usd is not None


async def test_unknown_chars_not_asked_again(api_fill, fake_deepseek):
    # Review Focus 3: chữ DeepSeek trả rỗng thì không hỏi lại
    fake_deepseek.responder = hanviet_responder({"赵": ["triệu"]})
    await _create(api_fill, "赵门\n", "书")
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(HanvietUnknown.char))).all() == ["门"]
    await _create(api_fill, "门赵\n", "书二")
    assert len(fake_deepseek.requests) == 1


async def test_fill_without_key_logs_info_and_never_raises(clean_db, data_dir, fake_translator):
    from app.deepseek.client import DeepSeekClient

    app = create_app(translator_factory=lambda: fake_translator, hanviet_autofill=True,
                     deepseek_client_factory=lambda: DeepSeekClient(api_key="", base_url="https://x.test"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await _create(client, "赵楷\n", "书")
    (line,) = [x for x in await log_rows(source="glossary") if "âm Hán Việt" in x.message]
    assert line.level == "info" and "DEEPSEEK_API_KEY" in line.message and line.tokens_in is None


# ---------- seed theo sha256, bổ sung nền có điều kiện ----------

SEED_HEAD = "char\treading\tsource\tconfidence\n"


async def test_seed_loads_even_after_ai_autofill_and_wins_over_ai(clean_db, tmp_path):
    # Review: bổ sung nền (ai) trước `make hanviet` không được chặn seed mãi mãi
    await put_readings({"赵": ["chiệu"], "楷": ["giai"]})  # ai, do autofill ghi
    await put_readings({"藏": ["tàng"]}, source="manual")
    seed = tmp_path / "seed.tsv"
    seed.write_text(SEED_HEAD + "赵\ttriệu\tconfirmed\t90\n楷\tkhải\tconfirmed\t90\n藏\ttạng\tconfirmed\t90\n门\tmôn\tai\t60\n",
                    encoding="utf-8")
    async with get_sessionmaker()() as s:
        assert await hanviet.ensure_seed(s, seed) == 3  # 赵, 楷, 门; 藏 đã có manual nên giữ nguyên
        await s.commit()
        assert await hanviet.seed_loaded(s)
    got = {(r.char, r.reading, r.source) for r in await readings_rows()}
    assert got == {("赵", "triệu", "confirmed"), ("楷", "khải", "confirmed"), ("藏", "tàng", "manual"), ("门", "môn", "ai")}


async def test_seed_never_overwrites_confirmed_rows(clean_db, tmp_path):
    await put_readings({"赵": ["triệu"]}, source="confirmed")
    seed = tmp_path / "seed.tsv"
    seed.write_text(SEED_HEAD + "赵\ttrịu\tconfirmed\t90\n", encoding="utf-8")
    async with get_sessionmaker()() as s:
        assert await hanviet.ensure_seed(s, seed) == 0
        await s.commit()
    assert {(r.char, r.reading) for r in await readings_rows()} == {("赵", "triệu")}


async def test_seed_reloads_when_file_changes(clean_db, tmp_path):
    seed = tmp_path / "seed.tsv"
    seed.write_text(SEED_HEAD + "赵\ttriệu\tconfirmed\t90\n", encoding="utf-8")
    async with get_sessionmaker()() as s:
        assert await hanviet.ensure_seed(s, seed) == 1
        await s.commit()
        assert await hanviet.ensure_seed(s, seed) == 0  # cùng hash: bỏ qua
        seed.write_text(SEED_HEAD + "赵\ttriệu\tconfirmed\t90\n门\tmôn\tconfirmed\t90\n", encoding="utf-8")
        assert await hanviet.ensure_seed(s, seed) == 1  # hash đổi: nạp thêm
        await s.commit()
    assert {r.char for r in await readings_rows()} == {"赵", "门"}


async def test_no_seed_means_no_autofill_and_warns_once(api_fill, fake_deepseek, caplog, monkeypatch):
    monkeypatch.setattr(hanviet, "_warned_no_seed", False)
    with caplog.at_level("WARNING"):
        await _create(api_fill, "赵楷雪藏\n苏清林凡\n", "书", seeded=False)
        await _create(api_fill, "赵楷雪藏\n苏清林凡\n", "书二", seeded=False)
    assert fake_deepseek.requests == []
    assert sum("make hanviet" in m for m in caplog.messages) == 1


def _han_text(n: int) -> str:
    chars = [chr(0x4E00 + i) for i in range(n)]
    return "\n".join("".join(chars[i:i + 20]) for i in range(0, n, 20)) + "\n"


async def test_autofill_is_capped_per_call_and_logs_overflow(api, fake_deepseek):
    await mark_seed_loaded()
    view = await upload(api, [("0001.txt", _han_text(350).encode())])
    await api.patch(f"/api/v1/imports/{view['import_id']}",
                    json={"chapters": [{"key": c["key"], "selected": True} for c in view["chapters"]]})
    r = await api.post("/api/v1/books", json={"title_zh": "多", "import_id": view["import_id"], "confirm_duplicate": True})
    big = r.json()["id"]
    fake_deepseek.responder = hanviet_responder({})
    import uuid
    await hanviet.fill_missing(uuid.UUID(big), fake_deepseek.client)
    assert sum(len(asked_chars(b)) for b in fake_deepseek.requests) == hanviet.MAX_AUTOFILL_CHARS == 300
    (line,) = [x for x in await log_rows(source="glossary") if "âm Hán Việt" in x.message]
    assert line.level == "warn" and "vượt giới hạn" in line.message and line.detail["overflow"] > 0


async def test_fill_missing_skips_when_engine_paused(api, fake_deepseek):
    import uuid

    from sqlalchemy import text

    await mark_seed_loaded()
    book_id, _ = await make_book(api, n_chapters=1, lines=2)
    async with get_sessionmaker()() as s:
        await s.execute(text("UPDATE worker_state SET paused = true WHERE engine = 'deepseek'"))
        await s.commit()
    assert await hanviet.fill_missing(uuid.UUID(book_id), fake_deepseek.client) == 0
    assert fake_deepseek.requests == []


async def test_fill_missing_is_serialized_per_book(api, fake_deepseek):
    import asyncio
    import uuid

    await mark_seed_loaded()
    book_id, _ = await make_book(api, n_chapters=1, lines=2)
    fake_deepseek.responder = hanviet_responder({})
    bid = uuid.UUID(book_id)
    await asyncio.gather(hanviet.fill_missing(bid, fake_deepseek.client), hanviet.fill_missing(bid, fake_deepseek.client))
    asked = [c for b in fake_deepseek.requests for c in asked_chars(b)]
    assert asked and len(asked) == len(set(asked))  # lượt thứ hai thấy các chữ đã hỏi, không hỏi lại
