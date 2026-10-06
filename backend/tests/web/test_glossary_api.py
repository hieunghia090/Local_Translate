import json
import uuid

import pytest
from sqlalchemy import select

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.models import GlossaryTerm, Job
from app.worker import Worker
from helpers import enqueue, make_book, seed_book

pytestmark = pytest.mark.db


async def create(api, book_id, **body):
    payload = {"src_zh": "赵楷", "dst_vi": "Triệu Khải", "category": "character", **body}
    return await api.post(f"/api/v1/books/{book_id}/glossary", json=payload)


async def test_crud_and_validation(api):
    book_id = await seed_book()
    r = await create(api, book_id, aliases=["Triệu Giai", " ", "Triệu Giai"])
    assert r.status_code == 201
    term = r.json()
    assert term["aliases"] == ["Triệu Giai"] and term["origin"] == "manual" and term["enabled"]
    assert (await create(api, book_id)).json()["error"]["code"] == "TERM_DUPLICATE"
    for bad in ({"src_zh": "Zhao"}, {"dst_vi": "  "}, {"category": "person"}, {"name_lang": "kr"}):
        assert (await create(api, book_id, **bad)).status_code == 422
    r = await api.patch(f"/api/v1/glossary/{term['id']}", json={"enabled": False})
    assert r.json()["term"]["enabled"] is False and r.json()["affected_chapter_ids"] == []
    items = (await api.get(f"/api/v1/books/{book_id}/glossary")).json()["items"]
    assert [i["src_zh"] for i in items] == ["赵楷"]
    assert (await api.delete(f"/api/v1/glossary/{term['id']}")).status_code == 204
    assert (await api.patch(f"/api/v1/glossary/{term['id']}", json={"enabled": True})).json()["error"]["code"] == "TERM_NOT_FOUND"


async def test_filter_and_sort(api):
    book_id = await seed_book()
    await create(api, book_id)
    await create(api, book_id, src_zh="东京", dst_vi="Đông Kinh", category="location")
    assert [i["src_zh"] for i in (await api.get(f"/api/v1/books/{book_id}/glossary", params={"category": "location"})).json()["items"]] == ["东京"]
    assert [i["src_zh"] for i in (await api.get(f"/api/v1/books/{book_id}/glossary", params={"q": "trieu"})).json()["items"]] == ["赵楷"]


async def test_occurrence_count_recomputed_in_background(api):
    # BR-4.4
    book_id, _ = await make_book(api, n_chapters=3, lines=2)
    r = await create(api, book_id, src_zh="第1章", dst_vi="Chương Một", category="term")
    term_id = r.json()["id"]
    item = next(i for i in (await api.get(f"/api/v1/books/{book_id}/glossary")).json()["items"] if i["id"] == term_id)
    assert item["occurrence_count"] == 3  # tiêu đề + 2 câu của chương 1


async def test_import_tsv_keep_existing(api):
    # AC-4.4, BR-4.5
    book_id = await seed_book()
    await create(api, book_id)
    await create(api, book_id, src_zh="东京", dst_vi="Đông Kinh", category="location")
    tsv = "src_zh\tdst_vi\tcategory\taliases\tenabled\n赵楷\tKhác\tcharacter\t\t1\n东京\tKhác\tlocation\t\t1\n汴梁\tBiện Lương\tlocation\tBiện Lương Thành\t1\n宗泽\tTông Trạch\tcharacter\t\ttrue\n"
    files = {"file": ("g.tsv", tsv.encode(), "text/tab-separated-values")}
    ask = (await api.post(f"/api/v1/books/{book_id}/glossary/import", data={"on_conflict": "ask"}, files=files)).json()
    assert ask["added"] == 0 and {c["src_zh"] for c in ask["conflicts"]} == {"赵楷", "东京"}
    r = await api.post(f"/api/v1/books/{book_id}/glossary/import", data={"on_conflict": "keep"},
                       files={"file": ("g.tsv", tsv.encode(), "text/plain")})
    assert r.json() == {"added": 2, "updated": 0, "kept": 2, "conflicts": []}
    items = {i["src_zh"]: i for i in (await api.get(f"/api/v1/books/{book_id}/glossary")).json()["items"]}
    assert items["赵楷"]["dst_vi"] == "Triệu Khải" and items["汴梁"]["aliases"] == ["Biện Lương Thành"]
    assert items["汴梁"]["origin"] == "import"


async def test_import_overwrite_selected_only(api):
    book_id = await seed_book()
    await create(api, book_id)
    await create(api, book_id, src_zh="东京", dst_vi="Đông Kinh", category="location")
    data = json.dumps([{"src_zh": "赵楷", "dst_vi": "A", "category": "character"},
                       {"src_zh": "东京", "dst_vi": "B", "category": "location"}])
    r = await api.post(f"/api/v1/books/{book_id}/glossary/import",
                       data={"on_conflict": "keep", "overwrite": json.dumps(["东京"])},
                       files={"file": ("g.json", data.encode(), "application/json")})
    assert r.json()["updated"] == 1 and r.json()["kept"] == 1
    items = {i["src_zh"]: i["dst_vi"] for i in (await api.get(f"/api/v1/books/{book_id}/glossary")).json()["items"]}
    assert items == {"赵楷": "Triệu Khải", "东京": "B"}


@pytest.mark.parametrize("content,name", [
    ("dst_vi\tcategory\nA\tcharacter\n", "missing.tsv"),
    ("src_zh\tdst_vi\tcategory\nZhao\tA\tcharacter\n", "latin.tsv"),
    ("src_zh\tdst_vi\tcategory\n赵\tA\tperson\n", "cat.tsv"),
    ("src_zh\tdst_vi\tcategory\n赵\tA\tcharacter\n赵\tB\tcharacter\n", "dup.tsv"),
    ('{"src_zh": "赵"}', "notlist.json"),
])
async def test_import_invalid_writes_nothing(api, content, name):
    # Review Focus 4
    book_id = await seed_book()
    r = await api.post(f"/api/v1/books/{book_id}/glossary/import", data={"on_conflict": "keep"},
                       files={"file": (name, content.encode(), "text/plain")})
    assert r.status_code == 422 and r.json()["error"]["code"] == "IMPORT_INVALID"
    assert r.json()["error"]["details"]["errors"]
    assert (await api.get(f"/api/v1/books/{book_id}/glossary")).json()["items"] == []


async def test_export_round_trip(api):
    book_id = await seed_book()
    await create(api, book_id, aliases=["Triệu Giai", "Triệu Giai Nhi"])
    tsv = (await api.get(f"/api/v1/books/{book_id}/glossary/export", params={"format": "tsv"})).text
    assert tsv.splitlines() == ["src_zh\tdst_vi\tcategory\taliases\tenabled", "赵楷\tTriệu Khải\tcharacter\tTriệu Giai|Triệu Giai Nhi\t1"]
    js = (await api.get(f"/api/v1/books/{book_id}/glossary/export", params={"format": "json"})).json()
    assert js[0]["aliases"] == ["Triệu Giai", "Triệu Giai Nhi"]


async def test_copy_from_other_book(api):
    # BR-4.6
    a, b = await seed_book(), await seed_book(title_zh="乙")
    await create(api, a)
    await create(api, a, src_zh="东京", dst_vi="Đông Kinh", category="location")
    await create(api, b)
    r = await api.post(f"/api/v1/books/{b}/glossary/copy", json={"from_book_id": str(a)})
    assert r.json() == {"added": 1, "skipped": 1}
    async with get_sessionmaker()() as s:
        copied = (await s.scalars(select(GlossaryTerm).where(GlossaryTerm.book_id == b, GlossaryTerm.src_zh == "东京"))).one()
    assert copied.origin == "copied"


async def test_changing_dst_lists_affected_chapters_then_bulk_retranslate(api):
    # AC-4.8
    book_id, chapters = await make_book(api, n_chapters=12, lines=1)
    term = (await create(api, book_id, src_zh="章第1句", dst_vi="câu đầu", category="term")).json()
    await enqueue(book_id)
    worker = Worker(FakeTranslator)
    while await worker.run_once():
        pass
    r = await api.patch(f"/api/v1/glossary/{term['id']}", json={"dst_vi": "câu thứ nhất"})
    affected = r.json()["affected_chapter_ids"]
    assert len(affected) == 12
    r = await api.post(f"/api/v1/books/{book_id}/chapters/bulk",
                       json={"action": "retranslate", "chapter_ids": affected, "keep_manual_edits": True})
    assert r.json()["affected"] == 12
    async with get_sessionmaker()() as s:
        assert len((await s.scalars(select(Job).where(Job.kind == "retranslate"))).all()) == 12


async def test_snippet(api, fake_translator):
    r = await api.post("/api/v1/translate/snippet", json={"text": "赵楷"})
    assert r.json() == {"vi": "VI<赵楷>"}
    assert (await api.post("/api/v1/translate/snippet", json={"text": "字" * 201})).status_code == 422
    assert (await api.post("/api/v1/translate/snippet", json={"text": "abc"})).json() == {"vi": "abc"}


async def test_unknown_book(api):
    r = await api.get(f"/api/v1/books/{uuid.uuid4()}/glossary")
    assert r.status_code == 404 and r.json()["error"]["code"] == "BOOK_NOT_FOUND"


async def test_chapter_view_has_spans_after_retranslate(api):
    # AC-6.5 (phần API)
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    await create(api, book_id, src_zh="第1章第1", dst_vi="Câu Một", category="term")
    body = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()
    seg = body["segments"][3]
    assert seg["glossary_spans"] == [{"term_id": body["glossary_terms"][0]["id"], "src": [0, 5], "dst": None}]
    assert body["glossary_terms"][0]["count"] == 1
    await enqueue(book_id)
    await Worker(FakeTranslator).run_once()
    seg = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()["segments"][3]
    assert seg["dst"] == "VI<Câu Một句话。>"
    assert seg["glossary_spans"][0]["dst"] == [3, 10]


async def test_import_commit_race_returns_409(api, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    book_id = await seed_book()
    real_commit = AsyncSession.commit

    async def boom(self):
        # refresh_terms (BR-4.15) đọc bảng âm nên autoflush: term mới đã nằm trong identity_map, không còn ở self.new
        if any(isinstance(o, GlossaryTerm) for o in (*self.new, *self.identity_map.values())):
            raise IntegrityError("insert", {}, Exception("duplicate"))
        return await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", boom)
    tsv = "src_zh\tdst_vi\n赵楷\tA\n"
    r = await api.post(f"/api/v1/books/{book_id}/glossary/import", data={"on_conflict": "keep"},
                       files={"file": ("g.tsv", tsv.encode(), "text/plain")})
    assert r.status_code == 409 and r.json()["error"]["code"] == "TERM_DUPLICATE"
