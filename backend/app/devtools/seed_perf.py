"""Dữ liệu đo NFR-2 (spec 00 mục 11): 50 truyện, truyện đầu có 2.000 chương.

Chỉ ghi DB, không ghi file nguồn. Dùng cho test hiệu năng và Playwright:
    cd backend && ../.venv/bin/python -m app.devtools.seed_perf
"""
import argparse
import asyncio
import sys
import uuid

from sqlalchemy import insert

from app.config import get_settings
from app.core.run_config import default_run_config
from app.db import get_engine, get_sessionmaker
from app.ids import uuid7
from app.models import DONE_STATUSES, Book, Chapter, LogEntry, utcnow
from app.services import logs as log_service

BIG_TITLE = "Truyện đo hiệu năng 2000 chương"
# Chu kỳ 10 chương. no % 10 == 9 luôn là `error`, nên e2e tìm được chương 1999 khi đang lọc "Lỗi".
STATUS_CYCLE = ("translated", "translated", "translated", "translated", "reviewed",
                "needs_review", "todo", "todo", "todo", "error")


def chapter_status(no: int) -> str:
    return STATUS_CYCLE[no % len(STATUS_CYCLE)]


async def seed_perf(books: int = 50, big_chapters: int = 2000, small_chapters: int = 40) -> uuid.UUID:
    big_id: uuid.UUID | None = None
    async with get_sessionmaker()() as s:
        await log_service.ensure_partitions(s)
        for i in range(books):
            big = i == 0
            book = Book(slug=f"perf-{i:03d}-{uuid7().hex[-6:]}", title_zh=f"性能测试{i:03d}",
                        title_vi=BIG_TITLE if big else f"Truyện đo hiệu năng {i:03d}", author="Perf",
                        genre="other", run_config=default_run_config("other"))
            s.add(book)
            await s.flush()
            rows = []
            for no in range(1, (big_chapters if big else small_chapters) + 1):
                status = chapter_status(no)
                rows.append({
                    "book_id": book.id, "no": no, "title_zh": f"第{no}章 测试标题{no}",
                    "title_vi": f"Chương {no}: Tiêu đề thử {no}", "status": status, "char_count": 3000,
                    "source_hash": "perf", "source_file": f"{no:04d}.txt",
                    "model_id": "HachimiMT-60" if status in DONE_STATUSES else None,
                })
            await s.execute(insert(Chapter), rows)
            now = utcnow()
            log_rows = [{"ts": now, "book_id": book.id, "chapter_no": r["no"], "level": "info", "source": "translate",
                         "message": f"Chương {r['no']} · dịch xong"} for r in rows if r["status"] in DONE_STATUSES]
            if log_rows:
                await s.execute(insert(LogEntry), log_rows)
            if big:
                big_id = book.id
        await s.commit()
    if big_id is None:
        raise ValueError("Cần ít nhất 1 truyện")
    return big_id


def guard_target(yes_main_db: bool, what: str = "50 truyện") -> str | None:
    """Trả về lời từ chối nếu DB đích không phải DB test và chưa có --yes-main-db."""
    settings = get_settings()
    if yes_main_db or settings.database_url == settings.test_database_url:
        return None
    return (f"Từ chối: DATABASE_URL không phải DB test, seed sẽ ghi {what} vào DB thật. "
            "Đặt DATABASE_URL trỏ tới DB test, hoặc thêm --yes-main-db nếu bạn chắc chắn.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.devtools.seed_perf")
    parser.add_argument("--books", type=int, default=50)
    parser.add_argument("--chapters", type=int, default=2000, help="số chương của truyện lớn")
    parser.add_argument("--small", type=int, default=40, help="số chương của mỗi truyện còn lại")
    parser.add_argument("--yes-main-db", action="store_true", help="cho phép ghi vào DB không phải DB test")
    args = parser.parse_args(argv)
    refusal = guard_target(args.yes_main_db)
    if refusal:
        print(refusal, file=sys.stderr)
        raise SystemExit(2)

    async def run() -> uuid.UUID:
        try:
            return await seed_perf(args.books, args.chapters, args.small)
        finally:
            await get_engine().dispose()

    print(asyncio.run(run()))


if __name__ == "__main__":
    main()
