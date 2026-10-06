import uuid
from datetime import datetime, timedelta, timezone

import pytest

from helpers import seed_book

pytestmark = pytest.mark.db

DONE = ["translated", "needs_review", "reviewed"]


async def seed_library():
    now = datetime.now(timezone.utc)
    ids = {
        "tong": await seed_book(title_zh="大宋有种", title_vi="Đại Tống Hữu Chủng", genre="modern_war",
                                statuses=["translated", "needs_review", "todo", "todo"],
                                last_opened_at=now - timedelta(days=2)),
        "tien": await seed_book(title_zh="仙逆", title_vi="Tiên Nghịch", author="耳根",
                                statuses=["reviewed"] * 3, last_opened_at=now - timedelta(days=1)),
        "phong": await seed_book(title_zh="我欲封天", title_vi="Ngã Dục Phong Thiên", statuses=["todo"] * 5),
        "mo": await seed_book(title_zh="神墓", title_vi=None, author="辰东", statuses=[]),
    }
    return ids


async def test_empty_library(api):
    # AC-1.7 (phần API)
    assert (await api.get("/api/v1/books")).json() == {"items": [], "next_cursor": None}
    assert (await api.get("/api/v1/library/stats")).json() == {
        "books": 0, "chapters_total": 0, "chapters_done": 0, "chapters_left": 0,
    }


async def test_list_and_stats_agree(api):
    # AC-1.1
    await seed_library()
    items = (await api.get("/api/v1/books")).json()["items"]
    stats = (await api.get("/api/v1/library/stats")).json()
    assert len(items) == 4
    assert stats == {"books": 4, "chapters_total": 12, "chapters_done": 5, "chapters_left": 7}
    assert sum(i["stats"]["total"] for i in items) == stats["chapters_total"]
    tong = next(i for i in items if i["title_zh"] == "大宋有种")
    assert tong["stats"] == {"total": 4, "todo": 2, "queued": 0, "translating": 0, "translated": 1,
                             "needs_review": 1, "reviewed": 0, "error": 0}
    assert (tong["progress_pct"], tong["state"], tong["genre"], tong["cover_url"]) == (50, "in_progress", "modern_war", None)


async def test_search_vietnamese_and_chinese(api):
    # AC-1.2, AC-1.3
    await seed_library()
    for q in ("Tống", "tong", "TỐNG", "大宋"):
        items = (await api.get("/api/v1/books", params={"q": q})).json()["items"]
        assert [i["title_zh"] for i in items] == ["大宋有种"], q
    by_author = (await api.get("/api/v1/books", params={"q": "辰东"})).json()["items"]
    assert [i["title_zh"] for i in by_author] == ["神墓"]


async def test_search_treats_wildcards_literally(api):
    await seed_library()
    assert (await api.get("/api/v1/books", params={"q": "%"})).json()["items"] == []


async def test_filters(api):
    # AC-1.4
    await seed_library()
    completed = (await api.get("/api/v1/books", params={"filter": "completed"})).json()["items"]
    assert [(i["title_zh"], i["state"]) for i in completed] == [("仙逆", "completed")]
    in_progress = (await api.get("/api/v1/books", params={"filter": "in_progress"})).json()["items"]
    assert {i["title_zh"] for i in in_progress} == {"大宋有种", "我欲封天", "神墓"}


async def test_sorts(api):
    await seed_library()
    recent = [i["title_zh"] for i in (await api.get("/api/v1/books")).json()["items"]]
    assert recent[:2] == ["仙逆", "大宋有种"]  # chưa mở bao giờ thì xếp sau
    by_name = [i["title_zh"] for i in (await api.get("/api/v1/books", params={"sort": "name"})).json()["items"]]
    assert by_name == ["大宋有种", "我欲封天", "仙逆", "神墓"]  # Đại, Ngã, Tiên, rồi tên gốc 神墓
    by_progress = (await api.get("/api/v1/books", params={"sort": "progress"})).json()["items"]
    assert [i["progress_pct"] for i in by_progress] == [100, 50, 0, 0]


async def test_open_moves_book_to_top(api):
    # AC-1.5
    ids = await seed_library()
    r = await api.post(f"/api/v1/books/{ids['phong']}/open")
    assert r.status_code == 204
    first = (await api.get("/api/v1/books", params={"sort": "recent"})).json()["items"][0]
    assert first["id"] == str(ids["phong"]) and first["last_opened_at"] is not None


async def test_open_unknown_book(api):
    r = await api.post(f"/api/v1/books/{uuid.uuid4()}/open")
    assert r.status_code == 404 and r.json()["error"]["code"] == "BOOK_NOT_FOUND"


async def test_invalid_query_values(api):
    assert (await api.get("/api/v1/books", params={"filter": "x"})).status_code == 422
    assert (await api.get("/api/v1/books", params={"sort": "x"})).status_code == 422
