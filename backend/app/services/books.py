import hashlib
import logging
import shutil
from collections.abc import Callable
from pathlib import Path

import anyio
from pydantic import ValidationError
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.glossary import nfc
from app.core.lines import has_chinese
from app.core.run_config import RunConfig, build_run_config, deep_merge
from app.core.slug import slugify, unique_slug
from app.core.titles import strip_ads
from app.core.translator import Translator
from app.errors import AppError
from app.models import Book, Chapter, GlossaryTerm, ImportSession, LogEntry, utcnow
from app.schemas import BookCreate, BookUpdate
from app.services import hanviet, imports, logs, queue
from app.services.import_analysis import default_title
from app.services.library import book_stats
from app.services.paths import books_root

log = logging.getLogger(__name__)
FALLBACK_SLUG = "truyen"


def _translate_book_title(title_zh: str, factory: Callable[[], Translator]) -> str | None:
    src = strip_ads(title_zh)
    if not has_chinese(src):
        return None
    try:
        out = factory().translate([src], beam=1, batch_size=1).outputs
    except Exception:
        log.warning("Không dịch được tên truyện", exc_info=True)
        return None
    return (out[0].strip() or None) if out else None


async def _pick_slug(session: AsyncSession, title_vi: str | None, title_zh: str) -> str:
    base = slugify(title_vi or "") or slugify(title_zh) or FALLBACK_SLUG
    taken = set((await session.scalars(select(Book.slug).where(Book.slug.like(f"{base}%")))).all())
    root = books_root()
    if root.exists():
        taken |= {p.name for p in root.iterdir() if p.name.startswith(base)}
    return unique_slug(base, taken)


def _write_sources(source_dir: Path, import_id, rows: list[tuple[dict, str | None]]) -> list[dict]:
    """Ghi source/NNNN.txt (UTF-8) cho các chương được giữ, đánh số lại từ 1."""
    out: list[dict] = []
    for no, (ch, title_edit) in enumerate(rows, start=1):
        data = imports.chapter_text_path(import_id, ch["key"]).read_text(encoding="utf-8").encode("utf-8")
        name = f"{no:04d}.txt"
        (source_dir / name).write_bytes(data)
        out.append({
            "no": no,
            "title_zh": ch["title_zh"],
            "title_vi": title_edit or default_title(ch, no),
            "title_vi_edited": bool(title_edit),
            "char_count": ch["chars"],
            "source_hash": hashlib.sha256(data).hexdigest(),
            "source_file": name,
        })
    return out


async def _load_import(session: AsyncSession, data: BookCreate) -> tuple[ImportSession | None, list]:
    if data.import_id is None:
        return None, []
    imp = await imports.get_import(session, data.import_id, lock=True)
    if imp.status == "parsing":
        raise AppError("IMPORT_NOT_READY", "Đang phân tích file…", 409)
    if imp.status == "failed":
        raise AppError("IMPORT_FAILED", imp.error or "Phân tích file thất bại", 409)
    rows = imports.selected_chapters(imp)
    if not rows:
        raise AppError("NO_CHAPTERS_SELECTED", "Chưa chọn chương nào", 422)
    return imp, rows


async def _apply_ai_init(session: AsyncSession, book: Book, imp: ImportSession | None, ai) -> None:
    """Spec 02 mục 7: đề xuất đã duyệt khi chạy thử thành GlossaryTerm, rồi xếp job ai_extract nếu bật AI."""
    preview = {item["id"]: item for item in (imp.ai_preview if imp is not None else [])}
    wanted = list(dict.fromkeys(ai.accepted_preview_ids))
    picked = [preview[i] for i in wanted if i in preview]
    if len(picked) < len(wanted):
        await logs.write_log(session, level="warn", source="glossary", book_id=book.id,
                             message=f"Bỏ {len(wanted) - len(picked)} đề xuất chạy thử không còn (file đã được phân tích lại)")
    terms: list[GlossaryTerm] = []
    for item in picked:
        edit = ai.preview_edits.get(item["id"])
        term = GlossaryTerm(book_id=book.id, src_zh=nfc(item["src_zh"]),
                            dst_vi=(edit and edit.dst_vi) or nfc(item["dst_vi"]),
                            category=(edit and edit.category) or item["category"], name_lang=item.get("name_lang"),
                            notes=item.get("notes"), aliases=[], enabled=True, origin="ai")
        session.add(term)
        terms.append(term)
    if terms:
        await session.flush()
        await hanviet.refresh_terms(session, terms)
        await hanviet.learn_from_terms(session, terms)
    if ai.enabled:
        ids = (await session.scalars(select(Chapter.id).where(Chapter.book_id == book.id)
                                     .order_by(Chapter.no).limit(ai.chapters))).all()
        if ids:
            await queue.enqueue_extract(session, book, chapter_ids=list(ids), model=ai.model, categories=ai.categories)


async def create_book(session: AsyncSession, data: BookCreate, translator_factory: Callable[[], Translator]) -> Book:
    if data.glossary.source != "none":
        raise AppError("GLOSSARY_SOURCE_UNSUPPORTED", "Chưa hỗ trợ nạp glossary khi tạo truyện", 422)
    try:
        run_config = build_run_config(data.genre, data.run_config)
    except ValidationError as e:
        errors = e.errors(include_url=False, include_context=False)
        raise AppError("INVALID_RUN_CONFIG", "Cấu hình dịch không hợp lệ", 422, {"errors": errors}) from e

    if not data.confirm_duplicate:
        dup = await session.scalar(
            select(Book.id).where(Book.title_zh == data.title_zh, Book.author.is_not_distinct_from(data.author)).limit(1)
        )
        if dup:
            raise AppError("BOOK_DUPLICATE", "Đã có truyện này. Vẫn tạo bản mới?", 409, {"book_id": str(dup)})

    imp, rows = await _load_import(session, data)
    title_vi = data.title_vi or await anyio.to_thread.run_sync(
        _translate_book_title, data.title_zh, translator_factory
    )
    for attempt in range(2):
        slug = await _pick_slug(session, title_vi, data.title_zh)
        book_dir = books_root() / slug
        try:
            book_dir.mkdir(parents=True)  # không exist_ok: không bao giờ ghi vào (hay xoá) thư mục có sẵn
            break
        except FileExistsError:
            if attempt == 1:  # có request khác vừa lấy slug này: chọn lại một lần
                raise
    try:
        source_dir = book_dir / "source"
        source_dir.mkdir()
        chapters = await anyio.to_thread.run_sync(_write_sources, source_dir, imp.id if imp else None, rows)
        book = Book(
            slug=slug,
            title_zh=data.title_zh,
            title_vi=title_vi,
            author=data.author,
            genre=data.genre,
            note=data.note,
            run_config=run_config,
            foundation_prompt=data.foundation_prompt,
        )
        session.add(book)
        await session.flush()
        if chapters:
            await session.execute(insert(Chapter), [{**c, "book_id": book.id} for c in chapters])
        await _apply_ai_init(session, book, imp, data.ai_extract)
        if imp is not None:
            await session.delete(imp)
        await session.commit()
    except BaseException:
        await session.rollback()
        shutil.rmtree(book_dir, ignore_errors=True)
        raise
    if imp is not None:
        shutil.rmtree(imports.import_dir(imp.id), ignore_errors=True)
    return book


def move_to_trash(path: Path) -> None:
    """Đưa thư mục vào thùng rác của hệ điều hành (không xoá hẳn)."""
    from send2trash import send2trash

    send2trash(str(path))


async def get_book_or_404(session: AsyncSession, book_id, *, lock: bool = False) -> Book:
    stmt = select(Book).where(Book.id == book_id)
    if lock:
        stmt = stmt.with_for_update()
    book = (await session.scalars(stmt)).first()
    if book is None:
        raise AppError("BOOK_NOT_FOUND", "Không tìm thấy truyện", 404)
    return book


async def get_book_detail(session: AsyncSession, book_id) -> dict:
    book = await get_book_or_404(session, book_id)
    stats = await book_stats(session, book.id)
    total_chars = await session.scalar(
        select(func.coalesce(func.sum(Chapter.char_count), 0)).where(Chapter.book_id == book.id)
    )
    return {
        "id": str(book.id),
        "slug": book.slug,
        "title_zh": book.title_zh,
        "title_vi": book.title_vi,
        "author": book.author,
        "genre": book.genre,
        "note": book.note,
        "cover_url": None,
        "run_config": book.run_config,
        "foundation_prompt": book.foundation_prompt,
        "total_chars": int(total_chars),
        "source_dir": str(books_root() / book.slug),
        "created_at": book.created_at,
        "updated_at": book.updated_at,
        "last_opened_at": book.last_opened_at,
        **stats,
    }


async def update_book(session: AsyncSession, book_id, data: BookUpdate) -> dict:
    book = await get_book_or_404(session, book_id, lock=True)
    for name in data.model_fields_set:
        value = getattr(data, name)
        if name == "run_config":
            merged = deep_merge(book.run_config, value or {})
            try:
                book.run_config = RunConfig.model_validate(merged).model_dump()
            except ValidationError as e:
                errors = e.errors(include_url=False, include_context=False)
                raise AppError("INVALID_RUN_CONFIG", "Cấu hình dịch không hợp lệ", 422, {"errors": errors}) from e
        elif name == "title_zh":
            if value:
                book.title_zh = value
        elif name == "genre":
            if value:
                book.genre = value  # BR-3.14: không tự đổi cấu hình xưng hô
        else:
            setattr(book, name, value or None)
    book.updated_at = utcnow()
    await session.commit()
    return await get_book_detail(session, book.id)


async def delete_book(session: AsyncSession, book_id, confirm: str) -> None:
    book = await get_book_or_404(session, book_id, lock=True)
    names = {n.strip() for n in (book.title_vi, book.title_zh) if n}
    if confirm.strip() not in names:
        raise AppError("CONFIRM_MISMATCH", "Tên truyện gõ lại không khớp", 422)
    folder = books_root() / book.slug
    await session.execute(delete(LogEntry).where(LogEntry.book_id == book.id))
    await session.delete(book)  # cascade: chương, segment, revision, job
    await session.flush()
    await session.commit()
    if folder.exists():  # DB đã commit: nếu không đưa vào thùng rác được thì chỉ cảnh báo
        try:
            await anyio.to_thread.run_sync(move_to_trash, folder)
        except Exception:
            log.warning("Không đưa thư mục truyện vào thùng rác: %s", folder, exc_info=True)
