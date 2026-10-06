"""Dữ liệu cho e2e/compare.spec.ts (spec 06 mục 4a): một truyện, chương 1 có đủ bản Hachimi và bản AI.

Chỉ ghi DB, không gọi model, không ghi file nguồn (trang Dịch chương đọc segment từ DB):
    cd backend && ../.venv/bin/python -m app.devtools.seed_compare
"""
import argparse
import asyncio
import sys

from sqlalchemy import insert

from app.core.run_config import default_run_config
from app.db import get_engine, get_sessionmaker
from app.devtools.seed_perf import guard_target as _guard
from app.ids import uuid7
from app.models import Book, Chapter, ChapterRevision, Segment, utcnow
from app.services.revisions import snapshot_rows

TITLE = "Truyện so sánh e2e"
MODEL = "deepseek-v4-pro"
ROWS = [  # (src, Hachimi, AI)
    ("第1章 比较", "Chương 1: So sánh", "Chương 1: So sánh"),
    ("在赵楷准备离开的时候。", "Khi Triệu Khải chuẩn bị rời đi.", "Lúc Triệu Khải sắp rời đi."),
    ("他笑了。", "Hắn cười.", "Hắn cười."),
    ("她走了。", "Nàng đi rồi.", "Cô ấy đi rồi."),
]


async def seed_compare() -> str:
    async with get_sessionmaker()() as s:
        book = Book(slug=f"cmp-{uuid7().hex[-6:]}", title_zh="比较测试", title_vi=TITLE, genre="other",
                    run_config=default_run_config("other"))
        s.add(book)
        await s.flush()
        ch = Chapter(book_id=book.id, no=1, title_zh="第1章 比较", title_vi="Chương 1: So sánh", status="translated",
                     char_count=30, source_hash="cmp", source_file="0001.txt", model_id=MODEL, translated_at=utcnow())
        s.add(ch)
        await s.flush()
        rows = [{"chapter_id": ch.id, "idx": i, "src": src, "is_meta": False, "dst": ai, "dst_machine": ai,
                 "dst_model_raw": ai, "dst_mt": mt, "dst_ai": ai, "edited": False}
                for i, (src, mt, ai) in enumerate(ROWS)]
        await s.execute(insert(Segment), rows)
        s.add(ChapterRevision(chapter_id=ch.id, kind="machine", model_id=MODEL, segments_changed=len(rows),
                              snapshot=snapshot_rows(rows), changed_idx=[]))
        await s.commit()
        return str(book.id)


async def _run() -> str:
    try:
        return await seed_compare()
    finally:
        await get_engine().dispose()


def guard_target(yes_main_db: bool) -> str | None:
    return _guard(yes_main_db, what="1 truyện so sánh")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.devtools.seed_compare")
    parser.add_argument("--yes-main-db", action="store_true", help="cho phép ghi vào DB không phải DB test")
    args = parser.parse_args(argv)
    refusal = guard_target(args.yes_main_db)
    if refusal:
        print(refusal, file=sys.stderr)
        raise SystemExit(2)
    print(asyncio.run(_run()))


if __name__ == "__main__":
    main()
