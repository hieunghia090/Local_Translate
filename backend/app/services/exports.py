import logging
import uuid
from datetime import datetime
from pathlib import Path

import anyio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.export import (
    MEDIA_TYPES,
    ExportChapter,
    ExportOptions,
    SegmentText,
    export_file_name,
    reserve_path,
    write_export,
)
from app.db import get_sessionmaker
from app.errors import AppError
from app.models import DONE_STATUSES, Book, Chapter, Export, Segment, utcnow
from app.schemas import ExportRequest
from app.services import logs
from app.services.books import get_book_or_404
from app.services.paths import books_root

log = logging.getLogger(__name__)
SCOPE_STATUSES = {"translated": DONE_STATUSES, "reviewed": ("reviewed",), "range": DONE_STATUSES}
LOAD_BATCH = 200  # số chương nạp segment mỗi lần


def _now() -> datetime:
    return datetime.now().astimezone()


def exports_dir(slug: str) -> Path:
    return books_root() / slug / "exports"


def check_range(scope: str, from_no: int | None, to_no: int | None) -> None:
    if scope != "range":
        return
    if from_no is None or to_no is None:
        raise AppError("INVALID_RANGE", "Cần nhập khoảng chương (từ # đến #)", 422)
    if from_no > to_no:
        raise AppError("INVALID_RANGE", "Chương bắt đầu phải nhỏ hơn hoặc bằng chương kết thúc", 422)


def _conds(book_id, scope: str, from_no: int | None, to_no: int | None) -> list:
    conds = [Chapter.book_id == book_id, Chapter.status.in_(SCOPE_STATUSES[scope])]
    if scope == "range":
        conds += [Chapter.no >= from_no, Chapter.no <= to_no]
    return conds


async def preview(session: AsyncSession, book_id, scope: str, from_no: int | None = None, to_no: int | None = None) -> dict:
    """BR-3.15: "Sẽ xuất N chương, bỏ qua M"."""
    await get_book_or_404(session, book_id)
    check_range(scope, from_no, to_no)
    total = await session.scalar(select(func.count()).select_from(Chapter).where(Chapter.book_id == book_id))
    n = await session.scalar(select(func.count()).select_from(Chapter).where(*_conds(book_id, scope, from_no, to_no)))
    return {"chapters": n, "skipped": total - n}


async def start_export(session: AsyncSession, book_id, req: ExportRequest) -> Export:
    counts = await preview(session, book_id, req.scope, req.from_no, req.to_no)
    if counts["chapters"] == 0:
        raise AppError("NOTHING_TO_EXPORT", "Không có chương nào đã dịch trong phạm vi này", 409)
    is_range = req.scope == "range"
    exp = Export(book_id=book_id, scope=req.scope, format=req.format,
                 from_no=req.from_no if is_range else None, to_no=req.to_no if is_range else None,
                 include_titles=req.include_titles, keep_meta=req.keep_meta, status="running",
                 chapters=counts["chapters"], skipped=counts["skipped"])
    session.add(exp)
    await session.commit()
    return exp


async def _load_chapters(session: AsyncSession, exp: Export) -> list[ExportChapter]:
    rows = (await session.execute(
        select(Chapter.id, Chapter.no, Chapter.title_zh, Chapter.title_vi)
        .where(*_conds(exp.book_id, exp.scope, exp.from_no, exp.to_no))
        .order_by(Chapter.no)
    )).all()
    out: list[ExportChapter] = []
    for i in range(0, len(rows), LOAD_BATCH):
        batch = rows[i:i + LOAD_BATCH]
        segs: dict[uuid.UUID, list[SegmentText]] = {r.id: [] for r in batch}
        result = await session.execute(
            select(Segment.chapter_id, Segment.src, Segment.dst, Segment.is_meta)
            .where(Segment.chapter_id.in_(list(segs)))
            .order_by(Segment.chapter_id, Segment.idx)
        )
        for cid, src, dst, is_meta in result:
            segs[cid].append(SegmentText(src, dst, is_meta))
        out += [ExportChapter(r.no, r.title_zh, r.title_vi, tuple(segs[r.id])) for r in batch]
    return out


def _discard_output(final: Path, book_dir: Path) -> None:
    final.unlink(missing_ok=True)
    for folder in (final.parent, book_dir):
        try:
            folder.rmdir()  # chỉ xoá khi rỗng
        except OSError:
            break


async def run_export(export_id: uuid.UUID) -> None:
    """Chạy nền trong process API: đọc DB, ghi file .part trong thread, rồi đổi tên thành file thật."""
    sessions = get_sessionmaker()
    part: Path | None = None
    try:
        async with sessions() as s:
            exp = await s.get(Export, export_id)
            if exp is None or exp.status != "running":
                return
            book = await s.get(Book, exp.book_id)
            if book is None:  # truyện vừa bị xoá: dòng export đã đi theo cascade
                return
            chapters = await _load_chapters(s, exp)
        if not chapters:
            raise AppError("NOTHING_TO_EXPORT", "Không còn chương nào đã dịch trong phạm vi này", 409)
        async with sessions() as s:
            if await s.get(Book, book.id) is None:  # xoá truyện trong lúc đang nạp chương
                return
        book_dir = books_root() / book.slug
        if not book_dir.is_dir():
            raise AppError("BOOK_FOLDER_MISSING", "Thư mục truyện không còn, không thể xuất file", 409)
        name = export_file_name(book.slug, exp.scope, exp.format, _now(), exp.from_no, exp.to_no)
        final, part = await anyio.to_thread.run_sync(reserve_path, exports_dir(book.slug), name)
        opts = ExportOptions(include_titles=exp.include_titles, keep_meta=exp.keep_meta)
        target = part
        await anyio.to_thread.run_sync(lambda: write_export(
            target, exp.format, chapters, opts,
            identifier=str(book.id), title=book.title_vi or book.title_zh, author=book.author,
        ))
        part.replace(final)
        part = None
        async with sessions() as s:
            res = await s.execute(update(Export).where(Export.id == export_id).values(
                status="done", file_name=final.name, chapters=len(chapters),
                size_bytes=final.stat().st_size, finished_at=utcnow(),
            ))
            if not res.rowcount:  # truyện bị xoá trong lúc ghi file: dọn file và thư mục rỗng
                await s.rollback()
                _discard_output(final, book_dir)
                return
            await logs.write_log(s, level="info", source="system", book_id=book.id,
                                 message=f"Đã xuất {len(chapters)} chương ra {final.name}",
                                 params={"scope": exp.scope, "format": exp.format})
            await s.commit()
    except Exception as e:
        log.exception("Xuất file lỗi")
        if part is not None:
            part.unlink(missing_ok=True)
        message = (e.message if isinstance(e, AppError) else str(e)) or type(e).__name__
        async with sessions() as s:
            await s.execute(update(Export).where(Export.id == export_id, Export.status == "running").values(
                status="failed", error=message[:500], finished_at=utcnow(),
            ))
            await s.commit()


def export_view(exp: Export) -> dict:
    return {
        "id": str(exp.id),
        "book_id": str(exp.book_id),
        "status": exp.status,
        "scope": exp.scope,
        "format": exp.format,
        "from_no": exp.from_no,
        "to_no": exp.to_no,
        "include_titles": exp.include_titles,
        "keep_meta": exp.keep_meta,
        "chapters": exp.chapters,
        "skipped": exp.skipped,
        "file_name": exp.file_name,
        "size_bytes": exp.size_bytes,
        "error": exp.error,
        "created_at": exp.created_at,
        "finished_at": exp.finished_at,
        "download_url": f"/api/v1/exports/{exp.id}/download" if exp.status == "done" else None,
    }


async def get_export(session: AsyncSession, export_id) -> Export:
    exp = await session.get(Export, export_id)
    if exp is None:
        raise AppError("EXPORT_NOT_FOUND", "Không tìm thấy lần xuất này", 404)
    return exp


async def download_target(session: AsyncSession, export_id) -> tuple[Path, str, str]:
    exp = await get_export(session, export_id)
    if exp.status != "done" or not exp.file_name:
        raise AppError("EXPORT_NOT_READY", "File chưa xuất xong", 409)
    book = await get_book_or_404(session, exp.book_id)
    path = exports_dir(book.slug) / Path(exp.file_name).name
    if not path.is_file():
        raise AppError("EXPORT_FILE_MISSING", "File xuất không còn trong thư mục exports", 404)
    return path, MEDIA_TYPES[exp.format], exp.file_name


async def fail_stale_exports(session: AsyncSession) -> int:
    """Gọi lúc khởi động: lần xuất còn `running` là do server tắt giữa chừng."""
    res = await session.execute(update(Export).where(Export.status == "running").values(
        status="failed", error="Server tắt giữa lúc xuất file", finished_at=utcnow(),
    ))
    return res.rowcount or 0


def remove_partial_files() -> int:
    """Gọi lúc khởi động: xoá file .part còn sót của lần xuất dở."""
    root = books_root()
    if not root.is_dir():
        return 0
    n = 0
    for path in root.glob("*/exports/*.part"):
        path.unlink(missing_ok=True)
        n += 1
    return n
