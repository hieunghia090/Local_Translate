"""Đề xuất sửa của DeepSeek (review_fixes, spec 08 mục 5) và soát một chương."""
import uuid
from collections import defaultdict

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compare import engine_column
from app.deepseek.catalog import is_deepseek_model
from app.errors import AppError
from app.models import ACTIVE_JOB_STATUSES, DONE_STATUSES, REVIEW_FIX_TYPES, Book, Chapter, ChapterNote, ChapterRevision, Job, ReviewFix, Segment, utcnow
from app.services import events, queue
from app.services.revisions import current_snapshot

AUTO_TYPES = ("name_mismatch", "missing_content")  # BR-8.20
AUTO_MIN_CONFIDENCE = 90
PENDING_FLAG = "review_pending"
BUSY = ("queued", "translating")


def reviewable(ch: Chapter) -> bool:
    """Mục 5.3: chương đã dịch bằng HachimiMT (không soát lại bản DeepSeek ở giai đoạn 1)."""
    return ch.status in DONE_STATUSES and bool(ch.model_id) and not is_deepseek_model(ch.model_id)


def fix_view(f: ReviewFix) -> dict:
    return {"id": str(f.id), "chapter_id": str(f.chapter_id), "segment_idx": f.segment_idx, "type": f.type,
            "before": f.before, "after": f.after, "reason": f.reason, "confidence": f.confidence, "status": f.status,
            "model_id": f.model_id, "created_at": f.created_at, "decided_at": f.decided_at}


def _indent(text: str) -> str:
    return text[: len(text) - len(text.lstrip())]


async def _chapter(session: AsyncSession, chapter_id, *, lock: bool = False) -> Chapter:
    stmt = select(Chapter).where(Chapter.id == chapter_id)
    if lock:
        stmt = stmt.with_for_update()
    ch = (await session.scalars(stmt)).first()
    if ch is None:
        raise AppError("CHAPTER_NOT_FOUND", "Không tìm thấy chương", 404)
    return ch


async def _segments(session: AsyncSession, chapter_id) -> dict[int, Segment]:
    rows = await session.scalars(select(Segment).where(Segment.chapter_id == chapter_id).order_by(Segment.idx).with_for_update())
    return {s.idx: s for s in rows}


async def _sync_pending_flags(session: AsyncSession, chapter_id, segs: dict[int, Segment]) -> int:
    pending = set((await session.scalars(
        select(ReviewFix.segment_idx).where(ReviewFix.chapter_id == chapter_id, ReviewFix.status == "pending"))).all())
    for idx, seg in segs.items():
        has = PENDING_FLAG in seg.flags
        if idx in pending and not has:
            seg.flags = [*seg.flags, PENDING_FLAG]
        elif idx not in pending and has:
            seg.flags = [f for f in seg.flags if f != PENDING_FLAG]
    return len(pending)


async def _apply(session: AsyncSession, chapter: Chapter, fixes: list[ReviewFix], segs: dict[int, Segment]) -> tuple[int, int]:
    """BR-8.21: dst = after, edited = false, một revision machine / note "review". Câu đã đổi từ lúc đề xuất thì không ghi đè."""
    now = utcnow()
    changed: list[int] = []
    stale = 0
    for f in fixes:
        seg = segs.get(f.segment_idx)
        if seg is None or seg.edited or seg.dst != f.before:
            f.status, f.decided_at = "rejected", now
            stale += 1
            continue
        # dst_model_raw cũng là `after`, honorific_edits rỗng: áp lại xưng hô (BR-7.14) đọc từ bản thô nên không được đè fix
        seg.dst = seg.dst_machine = seg.dst_model_raw = f.after
        setattr(seg, engine_column(chapter.model_id), f.after)  # BR-6.12: giữ dst_machine == cột engine hiện tại
        seg.honorific_edits = []
        seg.flags = [x for x in seg.flags if x != "honorific_rewritten"]
        seg.edited = False
        f.status, f.decided_at = "applied", now
        changed.append(seg.idx)
    if changed:
        session.add(ChapterRevision(chapter_id=chapter.id, kind="machine", model_id=fixes[0].model_id or chapter.model_id,
                                    run_config=None, segments_changed=len(changed), changed_idx=sorted(changed),
                                    snapshot=await current_snapshot(session, chapter.id), note="review",
                                    created_at=now, updated_at=now))
    return len(changed), stale


async def store_fixes(session: AsyncSession, chapter: Chapter, raw: list[dict], *, model_id: str, auto_apply: str) -> dict:
    """BR-8.19 → BR-8.22. Gọi trong transaction của pool, chương đã được khoá."""
    segs = await _segments(session, chapter.id)
    known = {(f.segment_idx, f.before, f.after): f.status
             for f in (await session.scalars(select(ReviewFix).where(ReviewFix.chapter_id == chapter.id))).all()}
    stats = {"proposed": len(raw), "stored": 0, "pending": 0, "auto_applied": 0, "dropped_mismatch": 0,
             "dropped_locked": 0, "dropped_rejected": 0, "dropped_invalid": 0, "duplicates": 0}
    now = utcnow()
    new: list[ReviewFix] = []
    for item in raw:
        try:
            idx = int(item["idx"])
            before = str(item.get("before") or "")
            after = str(item.get("after") or "").strip()
            typ = str(item.get("type") or "")
            confidence = int(item.get("confidence") or 0)
        except (KeyError, TypeError, ValueError):
            stats["dropped_invalid"] += 1
            continue
        seg = segs.get(idx)
        if typ not in REVIEW_FIX_TYPES or not after or seg is None or seg.is_meta or not seg.dst:
            stats["dropped_invalid"] += 1
            continue
        if seg.edited:  # BR-8.22
            stats["dropped_locked"] += 1
            continue
        if seg.dst.strip() != before.strip():  # BR-8.19
            stats["dropped_mismatch"] += 1
            continue
        after = _indent(seg.dst) + after
        if after == seg.dst:
            stats["dropped_invalid"] += 1
            continue
        key = (idx, seg.dst, after)
        status = known.get(key)
        if status == "rejected":  # BR-8.21: không đề xuất lại fix đã bỏ
            stats["dropped_rejected"] += 1
            continue
        if status == "pending":
            stats["duplicates"] += 1
            continue
        fix = ReviewFix(chapter_id=chapter.id, segment_idx=idx, type=typ, before=seg.dst, after=after,
                        reason=str(item.get("reason") or "").strip()[:500] or None,
                        confidence=max(0, min(100, confidence)), status="pending", model_id=model_id, created_at=now)
        session.add(fix)
        new.append(fix)
        known[key] = "pending"
    await session.flush()
    stats["stored"] = len(new)
    auto = [f for f in new if auto_apply == "high_confidence" and f.confidence >= AUTO_MIN_CONFIDENCE and f.type in AUTO_TYPES]
    if auto:
        stats["auto_applied"], _ = await _apply(session, chapter, auto, segs)
    stats["pending"] = await _sync_pending_flags(session, chapter.id, segs)
    if stats["pending"] and chapter.status in ("translated", "reviewed"):  # BR-8.20
        chapter.status = "needs_review"
    await events.emit_chapter(session, chapter)
    await events.emit_book_stats(session, chapter.book_id)
    return stats


async def supersede_pending(session: AsyncSession, chapter_id) -> int:
    """Chương vừa được dịch lại: các đề xuất đang chờ nhắm vào bản dịch cũ nên bị bỏ (reason = "superseded")."""
    res = await session.execute(
        update(ReviewFix).where(ReviewFix.chapter_id == chapter_id, ReviewFix.status == "pending")
        .values(status="rejected", reason="superseded", decided_at=utcnow()))
    return res.rowcount or 0


async def list_fixes(session: AsyncSession, chapter_id, status: str | None) -> list[dict]:
    await _chapter(session, chapter_id)
    stmt = select(ReviewFix).where(ReviewFix.chapter_id == chapter_id)
    if status:
        stmt = stmt.where(ReviewFix.status == status)
    return [fix_view(f) for f in await session.scalars(stmt.order_by(ReviewFix.segment_idx, ReviewFix.created_at))]


async def _pending_by_chapter(session: AsyncSession, ids: list[uuid.UUID]) -> tuple[dict, int]:
    fixes = (await session.scalars(
        select(ReviewFix).where(ReviewFix.id.in_(ids), ReviewFix.status == "pending")
        .order_by(ReviewFix.chapter_id, ReviewFix.created_at).with_for_update())).all()
    by_chapter: dict[uuid.UUID, list[ReviewFix]] = defaultdict(list)
    for f in fixes:
        by_chapter[f.chapter_id].append(f)
    return by_chapter, len(fixes)


async def apply_fixes(session: AsyncSession, ids: list[uuid.UUID]) -> dict:
    by_chapter, found = await _pending_by_chapter(session, ids)
    applied = stale = 0
    for chapter_id, fixes in by_chapter.items():
        chapter = await _chapter(session, chapter_id, lock=True)
        if chapter.status in BUSY:
            raise AppError("CHAPTER_BUSY", "Chương đang trong hàng đợi hoặc đang dịch", 409)
        segs = await _segments(session, chapter_id)
        a, s = await _apply(session, chapter, fixes, segs)
        applied, stale = applied + a, stale + s
        await _sync_pending_flags(session, chapter_id, segs)
        await events.emit_chapter(session, chapter)
        await events.emit_book_stats(session, chapter.book_id)
    await session.commit()
    return {"applied": applied, "stale": stale, "skipped": len(set(ids)) - found}


async def reject_fixes(session: AsyncSession, ids: list[uuid.UUID]) -> dict:
    by_chapter, found = await _pending_by_chapter(session, ids)
    now = utcnow()
    for chapter_id, fixes in by_chapter.items():
        chapter = await _chapter(session, chapter_id, lock=True)
        for f in fixes:
            f.status, f.decided_at = "rejected", now
        await _sync_pending_flags(session, chapter_id, await _segments(session, chapter_id))
        await events.emit_chapter(session, chapter)
    await session.commit()
    return {"rejected": found}


async def start_ai_review(session: AsyncSession, chapter_id) -> Job:
    chapter = await _chapter(session, chapter_id, lock=True)
    if not reviewable(chapter):
        raise AppError("CHAPTER_NOT_REVIEWABLE", "Chỉ soát được chương đã dịch bằng HachimiMT", 409)
    active = await session.scalar(select(Job.id).where(Job.chapter_id == chapter.id,
                                                       Job.status.in_(ACTIVE_JOB_STATUSES)).limit(1))
    if active is not None:
        raise AppError("CHAPTER_BUSY", "Chương đang có job trong hàng đợi", 409)
    book = await session.get(Book, chapter.book_id)
    jobs = await queue.enqueue_review(session, book, [chapter])
    await session.commit()
    return jobs[0]

def note_view(n: ChapterNote) -> dict:
    return {"id": str(n.id), "chapter_id": str(n.chapter_id), "type": n.type, "content": n.content,
            "resolved": n.resolved, "created_at": n.created_at, "updated_at": n.updated_at}


async def list_notes(session: AsyncSession, chapter_id) -> list[dict]:
    await _chapter(session, chapter_id)
    rows = await session.scalars(select(ChapterNote).where(ChapterNote.chapter_id == chapter_id)
                                 .order_by(ChapterNote.created_at, ChapterNote.id))
    return [note_view(n) for n in rows]


async def create_note(session: AsyncSession, chapter_id, *, type_: str, content: str) -> dict:
    await _chapter(session, chapter_id)
    now = utcnow()
    note = ChapterNote(chapter_id=chapter_id, type=type_, content=content, resolved=False, created_at=now, updated_at=now)
    session.add(note)
    await session.commit()
    return note_view(note)


async def _note(session: AsyncSession, note_id) -> ChapterNote:
    note = await session.get(ChapterNote, note_id)
    if note is None:
        raise AppError("NOTE_NOT_FOUND", "Không tìm thấy ghi chú", 404)
    return note


async def update_note(session: AsyncSession, note_id, changes: dict) -> dict:
    note = await _note(session, note_id)
    for key, value in changes.items():
        if value is not None:
            setattr(note, key, value)
    note.updated_at = utcnow()
    await session.commit()
    return note_view(note)


async def delete_note(session: AsyncSession, note_id) -> None:
    await session.delete(await _note(session, note_id))
    await session.commit()
