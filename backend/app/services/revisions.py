from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.models import Chapter, ChapterRevision, Segment, utcnow
from app.services import events

MANUAL_MERGE_WINDOW = timedelta(minutes=5)


def snapshot_rows(rows) -> list[dict]:
    """Ảnh chụp các câu không phải meta. `rows` là Segment hoặc dict cùng khoá."""

    def get(row, key):
        return row[key] if isinstance(row, dict) else getattr(row, key)

    return [
        {
            "idx": get(r, "idx"),
            "src": get(r, "src"),
            "dst": get(r, "dst"),
            "dst_machine": get(r, "dst_machine"),
            "edited": bool(get(r, "edited")),
        }
        for r in rows
        if not get(r, "is_meta")
    ]


async def current_snapshot(session: AsyncSession, chapter_id) -> list[dict]:
    await session.flush()
    segs = (await session.scalars(select(Segment).where(Segment.chapter_id == chapter_id).order_by(Segment.idx))).all()
    return snapshot_rows(segs)


async def record_manual_edit(session: AsyncSession, chapter: Chapter, idx: int) -> ChapterRevision:
    """BR-6.1: các lần sửa trong 5 phút (tính từ lần sửa đầu) gộp vào một revision manual."""
    now = utcnow()
    snapshot = await current_snapshot(session, chapter.id)
    last = (
        await session.scalars(
            select(ChapterRevision)
            .where(ChapterRevision.chapter_id == chapter.id)
            .order_by(ChapterRevision.created_at.desc())
            .limit(1)
        )
    ).first()
    if last is not None and last.kind == "manual" and last.note is None and now - last.created_at <= MANUAL_MERGE_WINDOW:
        changed = sorted(set(last.changed_idx) | {idx})
        last.changed_idx, last.segments_changed, last.snapshot, last.updated_at = changed, len(changed), snapshot, now
        return last
    rev = ChapterRevision(
        chapter_id=chapter.id, kind="manual", model_id=chapter.model_id, changed_idx=[idx], segments_changed=1,
        snapshot=snapshot, created_at=now, updated_at=now,
    )
    session.add(rev)
    return rev


def revision_view(rev: ChapterRevision) -> dict:
    cfg = rev.run_config or {}
    return {
        "id": str(rev.id),
        "kind": rev.kind,
        "model_id": rev.model_id,
        "beam": cfg.get("beam"),
        "chunk_mode": cfg.get("chunk_mode"),
        "segments_changed": rev.segments_changed,
        "note": rev.note,
        "created_at": rev.created_at,
        "updated_at": rev.updated_at,
        "restorable": rev.snapshot is not None,
    }


async def list_revisions(session: AsyncSession, chapter_id) -> list[dict]:
    from app.services.chapters import get_chapter_or_404  # tránh import vòng

    await get_chapter_or_404(session, chapter_id)
    rows = await session.scalars(
        select(ChapterRevision).where(ChapterRevision.chapter_id == chapter_id).order_by(ChapterRevision.created_at.desc())
    )
    return [revision_view(r) for r in rows]


async def restore_revision(session: AsyncSession, chapter_id, revision_id) -> dict:
    from app.services.chapters import BUSY, get_chapter_or_404

    ch = await get_chapter_or_404(session, chapter_id, lock=True)
    if ch.status in BUSY:
        raise AppError("CHAPTER_BUSY", "Chương đang trong hàng đợi hoặc đang dịch", 409)
    rev = await session.get(ChapterRevision, revision_id)
    if rev is None or rev.chapter_id != ch.id:
        raise AppError("REVISION_NOT_FOUND", "Không tìm thấy bản lưu", 404)
    if rev.snapshot is None:
        raise AppError("REVISION_NOT_RESTORABLE", "Bản lưu này không có nội dung để khôi phục", 409)
    segs = {s.idx: s for s in (await session.scalars(select(Segment).where(Segment.chapter_id == ch.id))).all()}
    restored, skipped, changed = 0, 0, []
    for item in rev.snapshot:
        seg = segs.get(item["idx"])
        if seg is None or seg.is_meta or seg.src != item["src"]:  # câu gốc đã khác: không ghép nhầm
            skipped += 1
            continue
        if seg.dst != item["dst"]:
            seg.dst = item["dst"]
            restored += 1
            changed.append(seg.idx)
        seg.edited = seg.dst != seg.dst_machine
    if restored == 0:  # không có gì đổi: không tạo revision, không đổi trạng thái
        await session.commit()
        return {"restored": 0, "skipped": skipped, "revision_id": None}
    if ch.status == "reviewed":
        ch.status = "needs_review"
    now = utcnow()
    new_rev = ChapterRevision(
        chapter_id=ch.id, kind="manual", model_id=ch.model_id, changed_idx=changed, segments_changed=restored,
        snapshot=await current_snapshot(session, ch.id), note=f"Khôi phục bản {rev.created_at.astimezone():%H:%M %d/%m}",
        created_at=now, updated_at=now,
    )
    session.add(new_rev)
    await session.flush()
    await events.emit_chapter(session, ch)
    await events.emit_book_stats(session, ch.book_id)
    await session.commit()
    return {"restored": restored, "skipped": skipped, "revision_id": str(new_rev.id)}
