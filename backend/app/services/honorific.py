import logging
import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compare import engine_column
from app.core.glossary import GlossaryIndex
from app.db import get_sessionmaker
from app.errors import AppError
from app.honorific.engine import SegInput, apply_chapter, classify
from app.models import Book, Chapter, ChapterRevision, Job, Segment, utcnow
from app.services import events, logs
from app.services import glossary as glossary_service
from app.services.revisions import snapshot_rows

log = logging.getLogger(__name__)
DONE = ("translated", "needs_review", "reviewed")


async def start_reapply(session: AsyncSession, book_id: uuid.UUID, chapter_ids: list[uuid.UUID] | None) -> tuple[Job, list[uuid.UUID]]:
    from app.services.books import get_book_or_404

    book = await get_book_or_404(session, book_id)
    stmt = (select(Chapter.id).where(Chapter.book_id == book.id, Chapter.status.in_(DONE))
            .where(or_(Chapter.model_id == "HachimiMT-60", Chapter.model_id.is_(None))).order_by(Chapter.no))
    if chapter_ids:
        stmt = stmt.where(Chapter.id.in_(chapter_ids))
    ids = list((await session.scalars(stmt)).all())
    if not ids:
        raise AppError("NOTHING_TO_REAPPLY", "Chưa có chương đã dịch để áp lại", 409)
    job = Job(book_id=book.id, chapter_id=ids[0] if len(ids) == 1 else None, kind="honorific_reapply", engine="ct2",
              status="running", progress=0, position=0, run_config=book.run_config, options={"chapter_ids": [str(i) for i in ids]},
              started_at=utcnow(), tokens_in=0, tokens_out=0, attempts=1)
    session.add(job)
    await session.commit()
    return job, ids


async def _reapply_one(session: AsyncSession, book: Book, chapter: Chapter) -> int:
    segs = list((await session.scalars(select(Segment).where(Segment.chapter_id == chapter.id).order_by(Segment.idx))).all())
    text = [s for s in segs if not s.is_meta]
    if not text:
        return 0
    if chapter.register_override:
        route, score = chapter.register_override, None
    else:
        route, score = classify("\n".join(s.src for s in segs), book.genre)
    index = await glossary_service.load_index(session, book.id)
    raws = [s.dst_model_raw if s.dst_model_raw is not None else (s.dst_machine or "") for s in text]
    outs, stats = apply_chapter(honorific_items([(s.src, r) for s, r in zip(text, raws)], index), route,
                                (book.run_config or {}).get("honorific") or {})
    changed = 0
    column = engine_column(chapter.model_id)  # chỉ chương HachimiMT được áp lại, nên là dst_mt (BR-6.12)
    for seg, raw, out in zip(text, raws, outs):
        setattr(seg, column, out.dst)
        if seg.edited:  # BR-7.14: làm mới bản máy / bản thô / edits, không đụng `dst` và cờ
            seg.dst_model_raw, seg.dst_machine, seg.honorific_edits = raw, out.dst, out.edits
            continue
        flags = [f for f in seg.flags if f != "honorific_rewritten"] + (["honorific_rewritten"] if out.edits else [])
        if seg.dst != out.dst or seg.honorific_edits != out.edits:
            changed += 1
        seg.dst_model_raw, seg.dst, seg.dst_machine, seg.honorific_edits, seg.flags = raw, out.dst, out.dst, out.edits, flags
    chapter.register_route, chapter.register_score = route, score
    now = utcnow()
    await session.flush()
    session.add(ChapterRevision(chapter_id=chapter.id, kind="machine", model_id=chapter.model_id, run_config=book.run_config,
                                segments_changed=changed, snapshot=snapshot_rows(segs), changed_idx=[],
                                note="honorific_reapply", created_at=now, updated_at=now))
    await logs.write_log(session, level="info", source="translate", book_id=book.id, chapter_id=chapter.id,
                         chapter_no=chapter.no, message=f"Chương {chapter.no} · áp lại xưng hô ({route}), đổi {changed} câu",
                         detail={"honorific": {"route": route, "score": score, **stats.to_json()}})
    await events.emit_chapter(session, chapter)
    return changed


async def reapply_chapters(book_id: uuid.UUID, chapter_ids: list[uuid.UUID], job_id: uuid.UUID) -> None:
    sessions = get_sessionmaker()
    try:
        for n, cid in enumerate(chapter_ids, start=1):
            async with sessions() as s:
                if await s.scalar(select(Job.status).where(Job.id == job_id)) != "running":  # đã huỷ: dừng, không ghi đè
                    return
                book = await s.get(Book, book_id)
                chapter = (await s.scalars(select(Chapter).where(Chapter.id == cid).with_for_update())).first()
                if book is None:
                    return
                if chapter is not None and chapter.status in DONE:
                    await _reapply_one(s, book, chapter)
                await s.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(progress=n * 100 // len(chapter_ids)))
                await s.commit()
        async with sessions() as s:
            await s.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(status="done", progress=100, finished_at=utcnow()))
            await events.emit_book_stats(s, book_id)
            await s.commit()
    except Exception as e:
        log.exception("Áp lại xưng hô lỗi")
        async with sessions() as s:
            await s.execute(update(Job).where(Job.id == job_id, Job.status == "running").values(status="failed", error=str(e)[:500], finished_at=utcnow()))
            await s.commit()


async def set_register(session: AsyncSession, chapter_id, route: str | None) -> tuple[Chapter, Job | None, list]:
    from app.services.chapters import get_chapter_or_404

    chapter = await get_chapter_or_404(session, chapter_id, lock=True)
    chapter.register_override = route
    await session.commit()
    if chapter.status not in DONE:
        return chapter, None, []
    job, ids = await start_reapply(session, chapter.book_id, [chapter.id])
    return chapter, job, ids


async def fail_stale_reapply_jobs(session: AsyncSession) -> int:
    res = await session.execute(update(Job).where(Job.kind == "honorific_reapply", Job.status == "running")
                                .values(status="failed", error="Server tắt giữa lúc áp lại", finished_at=utcnow()))
    return res.rowcount or 0


def protected_spans(index: GlossaryIndex, src: str, dst: str) -> tuple[tuple, tuple]:
    """Vùng glossary ở nguồn và ở đích. Lớp xưng hô không sửa trong các vùng này."""
    src_spans, dst_spans, used = [], [], {}
    for m in index.find(src):
        src_spans.append((m.start, m.end))
        start = -1
        for _ in range(used.get(m.term.id, 0) + 1):
            start = dst.find(m.term.dst, start + 1)
            if start < 0:
                break
        if start >= 0:
            dst_spans.append((start, start + len(m.term.dst)))
        used[m.term.id] = used.get(m.term.id, 0) + 1
    return tuple(src_spans), tuple(dst_spans)


def honorific_items(pairs: list[tuple[str, str]], index: GlossaryIndex) -> list[SegInput]:
    """pairs: (src, dst_raw) của các câu không phải meta, theo thứ tự."""
    out = []
    for src, raw in pairs:
        sp, dp = protected_spans(index, src, raw) if index else ((), ())
        out.append(SegInput(src, raw, sp, dp))
    return out
