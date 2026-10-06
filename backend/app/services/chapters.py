import hashlib
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import anyio
from pydantic import ValidationError
from sqlalchemy import delete, exists, func, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compare import versions_differ
from app.core.lines import has_chinese
from app.core.run_config import RunConfig, deep_merge
from app.core.segments import plan_segments
from app.core.splitter import chapter_from_file
from app.core.textio import SourceDecodeError, UnsupportedEncodingError, decode_source
from app.core.titles import default_title_vi, split_heading, strip_ads, translation_source
from app.core.translator import Translator
from app.errors import AppError
from app.models import ACTIVE_JOB_STATUSES, Book, Chapter, ChapterRevision, Job, Segment, utcnow
from app.schemas import ChapterUpdate
from app.services import events, logs, queue, revisions, versions
from app.services import glossary as glossary_service
from app.services.books import get_book_or_404
from app.services.paths import books_root, chapter_source_path
from app.services.review import reviewable

log = logging.getLogger(__name__)

BUSY = ("queued", "translating")
TRANSLATABLE = ("todo", "error")
RETRANSLATABLE = ("translated", "needs_review", "reviewed", "error")
REVIEWABLE = ("translated", "needs_review")
PAGE_MAX = 200
_ALLOWED = {"translate": TRANSLATABLE, "retranslate": RETRANSLATABLE, "mark_reviewed": REVIEWABLE}


def _escape_like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def chapter_row(ch: Chapter, last_run: dict | None, has_edits: bool, compare: dict | None = None) -> dict:
    return {
        "id": str(ch.id),
        "book_id": str(ch.book_id),
        "no": ch.no,
        "title_zh": ch.title_zh,
        "title_vi": ch.title_vi,
        "status": ch.status,
        "char_count": ch.char_count,
        "model_id": ch.model_id,
        "last_run": last_run,
        "has_manual_edits": has_edits,
        "error": ch.error,
        "translated_at": ch.translated_at,
        "reviewed_at": ch.reviewed_at,
        "updated_at": ch.updated_at,
        "compare": compare or dict(versions.EMPTY_COMPARE),
    }


async def get_chapter_or_404(session: AsyncSession, chapter_id, *, lock: bool = False) -> Chapter:
    stmt = select(Chapter).where(Chapter.id == chapter_id)
    if lock:
        # populate_existing: nếu đã nạp chương trước khi khoá thì phải đọc lại `no` mới nhất
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    ch = (await session.scalars(stmt)).first()
    if ch is None:
        raise AppError("CHAPTER_NOT_FOUND", "Không tìm thấy chương", 404)
    return ch


async def lock_book_then_chapter(session: AsyncSession, chapter_id) -> tuple[Book, Chapter]:
    """Thứ tự khoá luôn là truyện → chương, để các thao tác đánh số lại được tuần tự hoá theo truyện."""
    ch = await get_chapter_or_404(session, chapter_id)
    book = await get_book_or_404(session, ch.book_id, lock=True)
    ch = await get_chapter_or_404(session, chapter_id, lock=True)
    return book, ch


async def last_runs(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    if not ids:
        return {}
    rows = await session.execute(
        select(ChapterRevision.chapter_id, ChapterRevision.run_config, ChapterRevision.created_at)
        .ext(distinct_on(ChapterRevision.chapter_id))
        .where(ChapterRevision.chapter_id.in_(ids), ChapterRevision.kind == "machine", ChapterRevision.note.is_(None))
        .order_by(ChapterRevision.chapter_id, ChapterRevision.created_at.desc())
    )
    return {
        cid: {"beam": (cfg or {}).get("beam"), "chunk_mode": (cfg or {}).get("chunk_mode"), "at": at}
        for cid, cfg, at in rows
    }


async def edited_chapter_ids(session: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    rows = await session.scalars(select(Segment.chapter_id).where(Segment.chapter_id.in_(ids), Segment.edited).distinct())
    return set(rows.all())


def title_vi_search_ilike(raw: str):
    # '' phải là literal (không bind param) để khớp biểu thức của index ix_chapters_search_title_vi (migration 0008).
    return func.f_unaccent(func.lower(func.coalesce(Chapter.title_vi, literal_column("''")))).ilike(
        func.concat("%", func.f_unaccent(func.lower(raw)), "%")
    )


async def list_chapters(
    session: AsyncSession,
    book_id: uuid.UUID,
    *,
    statuses: list[str] | None = None,
    q: str | None = None,
    cursor: int | None = None,
    limit: int = 50,
) -> dict:
    await get_book_or_404(session, book_id)
    base = select(Chapter).where(Chapter.book_id == book_id)
    if statuses:
        base = base.where(Chapter.status.in_(statuses))
    if q and q.strip():
        term = q.strip()
        raw = _escape_like(term)
        conds = [
            Chapter.title_zh.ilike(f"%{raw}%"),
            title_vi_search_ilike(raw),
        ]
        if term.isdigit():
            conds.append(Chapter.no == int(term))
        base = base.where(or_(*conds))
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    page = base.where(Chapter.no > cursor) if cursor is not None else base
    rows = list((await session.scalars(page.order_by(Chapter.no).limit(limit + 1))).all())
    more = len(rows) > limit
    rows = rows[:limit]
    ids = [c.id for c in rows]
    runs = await last_runs(session, ids)
    edited = await edited_chapter_ids(session, ids)
    counts = await versions.compare_counts(session, ids)
    return {
        "items": [chapter_row(c, runs.get(c.id), c.id in edited, counts.get(c.id)) for c in rows],
        "next_cursor": str(rows[-1].no) if more and rows else None,
        "total": total,
    }


async def bulk_action(
    session: AsyncSession,
    book_id: uuid.UUID,
    *,
    action: str,
    chapter_ids: list[uuid.UUID] | None = None,
    statuses: list[str] | None = None,
    keep_manual_edits: bool | None = None,
    engine: str | None = None,
) -> dict:
    book = await get_book_or_404(session, book_id, lock=True)
    stmt = select(Chapter).where(Chapter.book_id == book.id)
    if chapter_ids is not None:
        stmt = stmt.where(Chapter.id.in_(chapter_ids))
    elif statuses:
        stmt = stmt.where(Chapter.status.in_(statuses))
    else:
        raise AppError("BULK_TARGET_REQUIRED", "Cần chọn chương hoặc bộ lọc trạng thái", 422)
    chapters = list((await session.scalars(stmt.order_by(Chapter.no).with_for_update())).all())
    if action == "review":  # BR-3.2a: chỉ chương đã dịch bằng HachimiMT
        jobs = await queue.enqueue_review(session, book, [c for c in chapters if reviewable(c)])
        await events.emit_book_stats(session, book.id)
        return {"affected": len(jobs), "skipped": len(chapters) - len(jobs), "job_ids": [str(j.id) for j in jobs]}
    eligible = [c for c in chapters if c.status in _ALLOWED[action]]
    result = {"affected": len(eligible), "skipped": len(chapters) - len(eligible), "job_ids": []}
    if not eligible:
        return result
    if action == "mark_reviewed":
        now = utcnow()
        for ch in eligible:
            ch.status, ch.reviewed_at = "reviewed", now
            await events.emit_chapter(session, ch)
    else:
        edited = await edited_chapter_ids(session, [c.id for c in eligible])
        if edited and keep_manual_edits is None:
            raise AppError(
                "CONFIRM_MANUAL_EDITS",
                f"{len(edited)} chương có câu sửa tay. Giữ lại các câu đó không?",
                409,
                {"chapters": len(edited)},
            )
        jobs = await queue.enqueue_chapters(
            session, book, eligible, kind=action, options={"keep_manual_edits": bool(keep_manual_edits)}, engine=engine
        )
        result["job_ids"] = [str(j.id) for j in jobs]
    await events.emit_book_stats(session, book.id)
    return result


def segment_view(seg) -> dict:
    return {"idx": seg.idx, "is_meta": seg.is_meta, "src": seg.src, "dst": seg.dst,
            "dst_machine": seg.dst_machine, "edited": seg.edited, "flags": seg.flags,
            "honorific_edits": seg.honorific_edits, "dst_mt": seg.dst_mt, "dst_ai": seg.dst_ai,
            "mt_ai_differ": None if seg.is_meta else versions_differ(seg.dst_mt, seg.dst_ai)}


def _spans(index, src: str, dst: str | None) -> list[dict]:
    spans: list[dict] = []
    claimed: list[tuple[int, int]] = []  # vùng dst đã gán cho span trước, span sau không chồng lên
    for m in index.find(src):
        dst_span = None
        if dst and m.term.dst:
            start = dst.find(m.term.dst)
            while start >= 0:
                end = start + len(m.term.dst)
                if not any(start < ce and cs < end for cs, ce in claimed):
                    dst_span = [start, end]
                    claimed.append((start, end))
                    break
                start = dst.find(m.term.dst, start + 1)
        spans.append({"term_id": m.term.id, "src": [m.start, m.end], "dst": dst_span})
    return spans


def _source_path(book: Book, ch: Chapter):
    return chapter_source_path(book.slug, ch.source_file or f"{ch.no:04d}.txt")


async def _has_edits(session: AsyncSession, chapter_id) -> bool:
    return bool(await session.scalar(select(exists().where(Segment.chapter_id == chapter_id, Segment.edited))))


async def chapter_by_no(session: AsyncSession, book_id, no: int) -> dict:
    book = await get_book_or_404(session, book_id)
    ch = (await session.scalars(select(Chapter).where(Chapter.book_id == book.id, Chapter.no == no))).first()
    if ch is None:
        raise AppError("CHAPTER_NOT_FOUND", "Không tìm thấy chương", 404)
    segs = (await session.scalars(select(Segment).where(Segment.chapter_id == ch.id).order_by(Segment.idx))).all()
    source_missing = False
    if segs:
        seg_rows = [segment_view(s) for s in segs]
    else:
        try:
            data = await anyio.to_thread.run_sync(_source_path(book, ch).read_bytes)
            plans = plan_segments(decode_source(data).text)
        except (OSError, SourceDecodeError):
            plans, source_missing = [], True
        seg_rows = [
            {"idx": p.idx, "is_meta": p.is_meta, "src": p.src, "dst": p.src if p.is_meta else None,
             "dst_machine": None, "edited": False, "flags": [], "honorific_edits": [],
             "dst_mt": None, "dst_ai": None, "mt_ai_differ": None}
            for p in plans
        ]
    index = await glossary_service.load_index(session, book.id)
    counts: dict[str, int] = {}
    for row in seg_rows:
        row["glossary_spans"] = [] if row["is_meta"] or not index else _spans(index, row["src"], row["dst"])
        for sp in row["glossary_spans"]:
            counts[sp["term_id"]] = counts.get(sp["term_id"], 0) + 1
    by_id = {t.id: t for t in index.terms}
    glossary_terms = sorted(
        ({"id": tid, "src_zh": by_id[tid].src, "dst_vi": by_id[tid].dst, "count": n} for tid, n in counts.items()),
        key=lambda x: -x["count"],
    )
    prev_no = await session.scalar(select(func.max(Chapter.no)).where(Chapter.book_id == book.id, Chapter.no < ch.no))
    next_no = await session.scalar(select(func.min(Chapter.no)).where(Chapter.book_id == book.id, Chapter.no > ch.no))
    job = (
        await session.scalars(
            select(Job).where(Job.chapter_id == ch.id, Job.status.in_(ACTIVE_JOB_STATUSES)).order_by(Job.created_at.desc())
        )
    ).first()
    runs = await last_runs(session, [ch.id])
    row = chapter_row(ch, runs.get(ch.id), any(s.get("edited") for s in seg_rows), versions.compare_summary(seg_rows))
    row["run_config_override"] = ch.run_config_override
    row["register_route"], row["register_score"], row["register_override"] = ch.register_route, ch.register_score, ch.register_override
    row["run_config"] = deep_merge(book.run_config, ch.run_config_override or {})
    return {
        "chapter": row,
        "segments": seg_rows,
        "prev_no": prev_no,
        "next_no": next_no,
        "job": {"id": str(job.id), "status": job.status, "progress": job.progress} if job else None,
        "source_missing": source_missing,
        "glossary_terms": glossary_terms,
    }


async def edit_segment(session: AsyncSession, chapter_id, idx: int, *, dst: str | None = None, revert: bool = False) -> dict:
    ch = await get_chapter_or_404(session, chapter_id, lock=True)
    if ch.status in BUSY:
        raise AppError("CHAPTER_BUSY", "Chương đang trong hàng đợi hoặc đang dịch, chưa sửa được", 409)
    seg = await session.get(Segment, (ch.id, idx))
    if seg is None:
        raise AppError("SEGMENT_NOT_FOUND", "Không tìm thấy câu", 404)
    if seg.is_meta:
        raise AppError("SEGMENT_IS_META", "Dòng meta không sửa được", 422)
    if revert:
        new_dst, edited = seg.dst_machine, False
    else:
        new_dst = dst.replace("\r", "")
        edited = new_dst != seg.dst_machine
    if new_dst != seg.dst or edited != seg.edited:
        seg.dst, seg.edited = new_dst, edited
        if ch.status == "reviewed":  # BR-0.2
            ch.status = "needs_review"
        await revisions.record_manual_edit(session, ch, idx)
        await events.emit_chapter(session, ch)
        await events.emit_book_stats(session, ch.book_id)
    await session.commit()
    return {"segment": segment_view(seg), "chapter": {"id": str(ch.id), "status": ch.status}}


async def mark_reviewed(session: AsyncSession, chapter_id) -> dict:
    ch = await get_chapter_or_404(session, chapter_id, lock=True)
    if ch.status not in REVIEWABLE:
        raise AppError("CHAPTER_NOT_REVIEWABLE", "Chỉ đánh dấu được chương đã dịch hoặc cần soát", 409)
    ch.status, ch.reviewed_at = "reviewed", utcnow()
    await events.emit_chapter(session, ch)
    await events.emit_book_stats(session, ch.book_id)
    await session.commit()
    return {"id": str(ch.id), "status": ch.status, "reviewed_at": ch.reviewed_at}


async def translate_chapter(
    session: AsyncSession, chapter_id, *, priority: bool = True, keep_manual_edits: bool | None = None,
    engine: str | None = None,
) -> Job:
    ch = await get_chapter_or_404(session, chapter_id, lock=True)
    if ch.status in BUSY:
        raise AppError("CHAPTER_BUSY", "Chương đang trong hàng đợi hoặc đang dịch", 409)
    if keep_manual_edits is None and await _has_edits(session, ch.id):
        raise AppError("CONFIRM_MANUAL_EDITS", "Chương có câu sửa tay. Giữ lại các câu đó không?", 409, {"chapters": 1})
    book = await session.get(Book, ch.book_id)
    kind = "translate" if ch.status == "todo" else "retranslate"
    (job,) = await queue.enqueue_chapters(
        session, book, [ch], kind=kind, options={"keep_manual_edits": bool(keep_manual_edits)}, engine=engine
    )
    if priority:  # BR-6.4: đứng đầu các job đang chờ
        front = await session.scalar(
            select(func.min(Job.position)).where(
                Job.engine == job.engine, Job.status.in_(("queued", "paused")), Job.id != job.id
            )
        )
        if front is not None and front <= job.position:
            job.position = front - 1
            await events.emit_job(session, job)
    await session.commit()
    return job


async def set_run_config(session: AsyncSession, chapter_id, override: dict | None) -> dict:
    ch = await get_chapter_or_404(session, chapter_id, lock=True)
    book = await session.get(Book, ch.book_id)
    if override is not None:
        try:
            RunConfig.model_validate(deep_merge(book.run_config, override))
        except ValidationError as e:
            errors = e.errors(include_url=False, include_context=False)
            raise AppError("INVALID_RUN_CONFIG", "Cấu hình dịch không hợp lệ", 422, {"errors": errors}) from e
    ch.run_config_override = override
    await session.commit()
    return {"run_config_override": ch.run_config_override,
            "run_config": deep_merge(book.run_config, ch.run_config_override or {})}


async def _move(session: AsyncSession, ch: Chapter, new_no: int) -> bool:
    """Đổi vị trí chương; trả True nếu có chương khác bị đánh số lại."""
    total = await session.scalar(select(func.count()).select_from(Chapter).where(Chapter.book_id == ch.book_id))
    new_no = max(1, min(new_no, total))
    if new_no == ch.no:
        return False
    if new_no < ch.no:
        await session.execute(
            update(Chapter)
            .where(Chapter.book_id == ch.book_id, Chapter.no >= new_no, Chapter.no < ch.no)
            .values(no=Chapter.no + 1)
            .execution_options(synchronize_session=False)
        )
    else:
        await session.execute(
            update(Chapter)
            .where(Chapter.book_id == ch.book_id, Chapter.no > ch.no, Chapter.no <= new_no)
            .values(no=Chapter.no - 1)
            .execution_options(synchronize_session=False)
        )
    ch.no = new_no
    return True


async def update_chapter(session: AsyncSession, chapter_id, data: ChapterUpdate) -> dict:
    _, ch = await lock_book_then_chapter(session, chapter_id)
    if "title_vi" in data.model_fields_set:
        ch.title_vi = (data.title_vi or "").strip() or None
        ch.title_vi_edited = ch.title_vi is not None  # BR-8.8: DeepSeek không ghi đè tiêu đề người dùng đặt
    shifted = data.no is not None and await _move(session, ch, data.no)
    await events.emit_chapter(session, ch)
    if shifted:
        await events.publish(session, "chapter.updated", ch.book_id, {"renumbered": True})
    await session.commit()
    await session.refresh(ch)  # updated_at do server sinh bị expire sau commit
    runs = await last_runs(session, [ch.id])
    return chapter_row(ch, runs.get(ch.id), await _has_edits(session, ch.id))


@dataclass(frozen=True)
class NewChapter:
    title_zh: str | None
    text: str


def _next_free_names(source_dir, count: int) -> list[str]:
    taken = {p.name for p in source_dir.iterdir()} if source_dir.exists() else set()
    names, n = [], len(taken) + 1
    while len(names) < count:
        name = f"{n:04d}.txt"
        if name not in taken:
            names.append(name)
            taken.add(name)
        n += 1
    return names


def _translate_titles(titles: list[str], factory: Callable[[], Translator]) -> list[str | None]:
    sources = [translation_source(t) for t in titles]
    pending = [s for s in dict.fromkeys(sources) if s and has_chinese(s)]
    done: dict[str, str] = {}
    if pending:
        try:
            out = factory().translate(pending, beam=1, batch_size=64).outputs
            done = {s: o.strip() for s, o in zip(pending, out)}
        except Exception:
            log.warning("Không dịch được tiêu đề chương mới", exc_info=True)
    return [(done.get(s) or None) if s and has_chinese(s) else (s or None) for s in sources]


async def add_chapters(
    session: AsyncSession,
    book_id,
    items: list[NewChapter],
    *,
    after_no: int | None,
    translator_factory: Callable[[], Translator],
) -> list[dict]:
    drafts = []
    for item in items:
        text = item.text.replace("\r\n", "\n").strip("\n")
        title = (item.title_zh or "").strip()
        if title and text.split("\n", 1)[0].strip() != title:
            text = f"{title}\n{text}"
        draft = chapter_from_file("chapter.txt", text)
        if draft.chars == 0:
            raise AppError("EMPTY_CHAPTER", "Chương không có chữ Hán nào", 422)
        drafts.append(draft)
    if not drafts:
        raise AppError("EMPTY_CHAPTER", "Chưa có nội dung chương", 422)

    # Dịch tiêu đề (chậm) trước khi giữ khoá truyện
    machine = await anyio.to_thread.run_sync(_translate_titles, [d.title_zh for d in drafts], translator_factory)
    book = await get_book_or_404(session, book_id, lock=True)
    total = await session.scalar(select(func.count()).select_from(Chapter).where(Chapter.book_id == book.id))
    insert_at = total if after_no is None else max(0, min(after_no, total))
    shifted = await session.execute(
        update(Chapter)
        .where(Chapter.book_id == book.id, Chapter.no > insert_at)
        .values(no=Chapter.no + len(drafts))
        .execution_options(synchronize_session=False)
    )
    source_dir = books_root() / book.slug / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    names = _next_free_names(source_dir, len(drafts))
    created: list[Chapter] = []
    written: list = []
    try:
        for i, (draft, name, vi) in enumerate(zip(drafts, names, machine), start=1):
            data = draft.text.encode("utf-8")
            written.append(source_dir / name)
            await anyio.to_thread.run_sync((source_dir / name).write_bytes, data)
            no = insert_at + i
            ch = Chapter(
                book_id=book.id, no=no, title_zh=draft.title_zh,
                title_vi=default_title_vi(split_heading(strip_ads(draft.title_zh))[0], no, vi),
                status="todo", char_count=draft.chars, source_hash=hashlib.sha256(data).hexdigest(), source_file=name,
            )
            session.add(ch)
            created.append(ch)
        await session.flush()
        await logs.write_log(session, level="info", source="system", book_id=book.id,
                             message=f"Thêm {len(created)} chương (từ vị trí {insert_at + 1})")
        if shifted.rowcount:
            await events.publish(session, "chapter.updated", book.id, {"renumbered": True})
        await events.emit_book_stats(session, book.id)
        await session.commit()
    except BaseException:
        for f in written:
            await anyio.to_thread.run_sync(lambda f=f: f.unlink(missing_ok=True))
        raise
    for c in created:
        await session.refresh(c)  # updated_at do server sinh bị expire sau commit
    return [chapter_row(c, None, False) for c in created]


async def replace_source(session: AsyncSession, chapter_id, data: bytes) -> dict:
    ch = await get_chapter_or_404(session, chapter_id, lock=True)
    if ch.status in BUSY:
        raise AppError("CHAPTER_BUSY", "Chương đang trong hàng đợi hoặc đang dịch", 409)
    try:
        text = decode_source(data).text
    except (SourceDecodeError, UnsupportedEncodingError) as e:
        raise AppError("ENCODING", f"{e}. Thử lưu file ở UTF-8.", 422) from e
    draft = chapter_from_file(ch.source_file or "chapter.txt", text)
    if draft.chars == 0:
        raise AppError("EMPTY_CHAPTER", "Chương không có chữ Hán nào", 422)
    new_bytes = draft.text.encode("utf-8")
    new_hash = hashlib.sha256(new_bytes).hexdigest()
    if new_hash == ch.source_hash:
        return {"changed": False, "chapter": chapter_row(ch, None, await _has_edits(session, ch.id))}
    book = await session.get(Book, ch.book_id)
    snapshot = await revisions.current_snapshot(session, ch.id)
    if snapshot:  # BR-0.4: giữ bản dịch cũ thành một revision
        now = utcnow()
        session.add(ChapterRevision(chapter_id=ch.id, kind="machine", model_id=ch.model_id, snapshot=snapshot,
                                    changed_idx=[], segments_changed=0, note="Bản dịch trước khi thay bản gốc",
                                    created_at=now, updated_at=now))
    await session.execute(delete(Segment).where(Segment.chapter_id == ch.id))
    ch.source_file = ch.source_file or f"{ch.no:04d}.txt"
    path = _source_path(book, ch)
    tmp = path.with_name(path.name + ".tmp")  # ghi tạm, commit DB xong mới đổi tên đè file thật
    await anyio.to_thread.run_sync(tmp.write_bytes, new_bytes)
    ch.source_hash, ch.char_count, ch.title_zh = new_hash, draft.chars, draft.title_zh
    ch.status, ch.model_id, ch.translated_at, ch.reviewed_at, ch.error = "todo", None, None, None, None
    await logs.write_log(session, level="info", source="system", book_id=ch.book_id, chapter_id=ch.id,
                         chapter_no=ch.no, message=f"Thay bản gốc chương {ch.no}")
    try:
        await events.emit_chapter(session, ch)
        await events.emit_book_stats(session, ch.book_id)
        await session.commit()
    except BaseException:
        await anyio.to_thread.run_sync(lambda: tmp.unlink(missing_ok=True))
        raise
    await anyio.to_thread.run_sync(os.replace, tmp, path)
    await session.refresh(ch)
    return {"changed": True, "chapter": chapter_row(ch, None, False)}


async def delete_chapter(session: AsyncSession, chapter_id) -> None:
    book, ch = await lock_book_then_chapter(session, chapter_id)
    if ch.status in BUSY:
        raise AppError("CHAPTER_BUSY", "Chương đang trong hàng đợi hoặc đang dịch", 409)
    path, no, book_id = _source_path(book, ch), ch.no, ch.book_id
    await session.delete(ch)
    await session.flush()
    shifted = await session.execute(
        update(Chapter)
        .where(Chapter.book_id == book_id, Chapter.no > no)
        .values(no=Chapter.no - 1)
        .execution_options(synchronize_session=False)
    )
    await logs.write_log(session, level="info", source="system", book_id=book_id, message=f"Xoá chương {no}")
    await events.publish(session, "chapter.updated", book_id, {"id": str(chapter_id), "deleted": True})
    if shifted.rowcount:
        await events.publish(session, "chapter.updated", book_id, {"renumbered": True})
    await events.emit_book_stats(session, book_id)
    await session.commit()
    await anyio.to_thread.run_sync(lambda: path.unlink(missing_ok=True))
