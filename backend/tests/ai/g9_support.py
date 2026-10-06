import json
import uuid

from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import Chapter, GlossarySuggestion, HanvietReading, Job, utcnow
from fake_deepseek import reply, user_of
from helpers import upload

LINE = "赵楷对苏清雪说：今天天气很好。"


def story(no: int, body: str) -> str:
    return f"第{no}章 风云{no}\n\n{body}\n"


async def import_from_texts(api, texts: list[str]) -> dict:
    """ImportSession đã phân tích, mọi chương được giữ (chương ngắn mặc định bị bỏ chọn)."""
    view = await upload(api, [(f"{i:04d}.txt", t.encode()) for i, t in enumerate(texts, start=1)])
    r = await api.patch(f"/api/v1/imports/{view['import_id']}",
                        json={"chapters": [{"key": c["key"], "selected": True} for c in view["chapters"]]})
    assert r.status_code == 200, r.text
    return r.json()


async def book_from_texts(api, texts: list[str], *, title: str = "书", **extra) -> tuple[str, list[Chapter]]:
    view = await import_from_texts(api, texts)
    r = await api.post("/api/v1/books", json={"title_zh": title, "title_vi": title, "import_id": view["import_id"],
                                              "confirm_duplicate": True, **extra})
    assert r.status_code == 201, r.text
    book_id = r.json()["id"]
    async with get_sessionmaker()() as s:
        chapters = (await s.scalars(select(Chapter).where(Chapter.book_id == uuid.UUID(book_id)).order_by(Chapter.no))).all()
    return book_id, list(chapters)


async def ds_texts(api, texts: list[str], *, concurrency: int = 1, auto_extract: bool = False):
    """Truyện engine DeepSeek từ văn bản tuỳ ý (như ds_book của G8)."""
    book_id, chapters = await book_from_texts(api, texts)
    r = await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {
        "engine": "deepseek", "model_id": "deepseek-v4-pro",
        "deepseek": {"concurrency": concurrency, "auto_extract_glossary": auto_extract}}})
    assert r.status_code == 200, r.text
    return book_id, chapters


def term(src: str, dst: str, type_: str = "character", conf: int = 95, lang: str | None = "zh", notes: str = "") -> dict:
    return {"type": type_, "source_term": src, "suggested_target": dst, "name_lang": lang, "confidence": conf, "notes": notes}


def terms_reply(*items, prompt: int = 1000, completion: int = 200, cached: int = 0):
    return reply(json.dumps({"terms": list(items)}, ensure_ascii=False), prompt=prompt, completion=completion, cached=cached)


def is_extract(body: dict) -> bool:
    return "# LOẠI CẦN TRÍCH" in user_of(body)


def asked_chars(body: dict) -> list[str]:
    return user_of(body).split("\n", 1)[1].split()


def hanviet_responder(table: dict[str, list[str]]):
    def respond(body):
        return reply(json.dumps({c: table.get(c, []) for c in asked_chars(body)}, ensure_ascii=False),
                     prompt=200, completion=120)
    return respond


async def put_readings(table: dict[str, list[str]], source: str = "ai") -> None:
    conf = {"ai": 60, "confirmed": 90, "learned": 85, "manual": 100}[source]
    async with get_sessionmaker()() as s:
        for c, rs in table.items():
            for r in rs:
                s.add(HanvietReading(char=c, reading=r, source=source, confidence=conf, created_at=utcnow()))
        await s.commit()


async def readings_rows() -> list[HanvietReading]:
    async with get_sessionmaker()() as s:
        return list((await s.scalars(select(HanvietReading).order_by(HanvietReading.char, HanvietReading.reading))).all())


async def suggestions(book_id, status: str | None = None) -> list[GlossarySuggestion]:
    async with get_sessionmaker()() as s:
        stmt = select(GlossarySuggestion).where(GlossarySuggestion.book_id == uuid.UUID(str(book_id)))
        if status:
            stmt = stmt.where(GlossarySuggestion.status == status)
        return list((await s.scalars(stmt.order_by(GlossarySuggestion.src_zh))).all())


async def add_suggestion(book_id, src: str, dst: str, conf: int = 90, category: str = "character") -> str:
    async with get_sessionmaker()() as s:
        now = utcnow()
        sg = GlossarySuggestion(book_id=uuid.UUID(str(book_id)), src_zh=src, dst_vi=dst, category=category,
                                confidence=conf, occurrence_count=2, provider="deepseek", model="deepseek-v4-pro",
                                status="pending", created_at=now, updated_at=now)
        s.add(sg)
        await s.commit()
        return str(sg.id)


async def jobs(kind: str | None = None) -> list[Job]:
    async with get_sessionmaker()() as s:
        stmt = select(Job).order_by(Job.position)
        if kind:
            stmt = stmt.where(Job.kind == kind)
        return list((await s.scalars(stmt)).all())


async def mark_seed_loaded(digest: str = "test-seed") -> None:
    """Coi như đã nạp seed Hán Việt (bổ sung nền bằng DeepSeek chỉ chạy sau khi nạp seed)."""
    from app.services import hanviet

    async with get_sessionmaker()() as s:
        await hanviet._meta_set(s, hanviet.SEED_META_KEY, digest)
        await s.commit()
