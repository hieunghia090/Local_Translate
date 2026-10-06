"""Đề xuất glossary từ AI (spec 04 mục 6, BR-4.7, BR-4.8, BR-4.12) và trang kiểm tra glossary (BR-4.14, BR-4.20)."""
import re
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.glossary import nfc
from app.ids import uuid7
from app.models import GlossarySuggestion, GlossaryTerm, Job, utcnow
from app.services import events, hanviet
from app.services.books import get_book_or_404
from app.services.glossary import term_view

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


LOW_CONFIDENCE = 80  # BR-4.7


def suggestion_view(sg: GlossarySuggestion) -> dict:
    return {"id": str(sg.id), "src_zh": sg.src_zh, "dst_vi": sg.dst_vi, "category": sg.category, "name_lang": sg.name_lang,
            "notes": sg.notes, "context": sg.context, "confidence": sg.confidence, "occurrence_count": sg.occurrence_count,
            "provider": sg.provider, "model": sg.model, "status": sg.status,
            "selected": sg.confidence >= LOW_CONFIDENCE, "created_at": sg.created_at}


def extract_job_view(job: Job) -> dict:
    opts = job.options or {}
    return {"id": str(job.id), "status": job.status, "progress": job.progress, "error": job.error,
            "auto": bool(opts.get("auto")), "chapters": len(opts.get("chapter_ids") or []),
            "created_at": job.created_at, "finished_at": job.finished_at}


def _filter_clauses(book_id, flt) -> list:
    clauses = [GlossarySuggestion.book_id == book_id, GlossarySuggestion.status == "pending"]
    if flt.min_confidence is not None:
        clauses.append(GlossarySuggestion.confidence >= flt.min_confidence)
    if flt.max_confidence is not None:
        clauses.append(GlossarySuggestion.confidence <= flt.max_confidence)
    if flt.exclude_ids:
        clauses.append(GlossarySuggestion.id.not_in(flt.exclude_ids))
    return clauses


async def list_suggestions(session: AsyncSession, book_id, status: str | None, *, limit: int = 200, offset: int = 0) -> dict:
    await get_book_or_404(session, book_id)
    where = [GlossarySuggestion.book_id == book_id]
    if status:
        where.append(GlossarySuggestion.status == status)
    total = await session.scalar(select(func.count()).select_from(GlossarySuggestion).where(*where))
    rows = await session.scalars(select(GlossarySuggestion).where(*where).order_by(
        GlossarySuggestion.occurrence_count.desc(), GlossarySuggestion.confidence.desc(), GlossarySuggestion.src_zh,
        GlossarySuggestion.id).limit(limit).offset(offset))
    job = (await session.scalars(select(Job).where(Job.book_id == book_id, Job.kind == "ai_extract")
                                 .order_by(Job.created_at.desc(), Job.position.desc()).limit(1))).first()
    return {"items": [suggestion_view(r) for r in rows], "total": int(total or 0), "limit": limit, "offset": offset,
            "last_job": extract_job_view(job) if job else None}


ACCEPT_CHUNK = 2000


async def _accept_chunk(session: AsyncSession, book_id, by_id: dict) -> tuple[int, int, int, list[uuid.UUID]]:
    rows = (await session.scalars(select(GlossarySuggestion).where(
        GlossarySuggestion.id.in_(list(by_id)), GlossarySuggestion.book_id == book_id,
        GlossarySuggestion.status == "pending").with_for_update())).all()
    added = existing = 0
    new_ids: list[uuid.UUID] = []
    now = utcnow()
    for sg in rows:
        edit = by_id[sg.id]
        dst, category = (edit and edit.dst_vi) or nfc(sg.dst_vi), (edit and edit.category) or sg.category
        tid = (await session.execute(
            pg_insert(GlossaryTerm).values(id=uuid7(), book_id=book_id, src_zh=nfc(sg.src_zh), dst_vi=dst, category=category,
                                           name_lang=sg.name_lang, notes=sg.notes, aliases=[], enabled=True, origin="ai",
                                           occurrence_count=sg.occurrence_count)
            .on_conflict_do_nothing(constraint="uq_glossary_book_src").returning(GlossaryTerm.id)
        )).scalar_one_or_none()
        if tid is None:
            existing += 1
        else:
            added += 1
            new_ids.append(tid)
        sg.status, sg.dst_vi, sg.category, sg.decided_at, sg.updated_at = "accepted", dst, category, now, now
    for i in range(0, len(new_ids), ACCEPT_CHUNK):
        terms = list((await session.scalars(select(GlossaryTerm).where(GlossaryTerm.id.in_(new_ids[i:i + ACCEPT_CHUNK])))).all())
        await hanviet.refresh_terms(session, terms)  # BR-4.15: tính lại khi chấp nhận
        await hanviet.learn_from_terms(session, terms)  # mục 6a bước 4, AC-4.17
    return added, existing, len(by_id) - len(rows), new_ids


async def accept(session: AsyncSession, book_id, items, flt=None) -> dict:
    """BR-4.8 / BR-4.12: mỗi mục pending thành GlossaryTerm (origin = ai) bằng INSERT … ON CONFLICT DO NOTHING.
    `items` (có thể kèm bản sửa) và/hoặc `flt` (mọi mục pending khớp bộ lọc, trừ các mục trong items)."""
    await get_book_or_404(session, book_id, lock=True)
    by_id: dict = {i.id: i for i in items}
    if flt is not None:
        clauses = _filter_clauses(book_id, flt)
        ids = (await session.scalars(select(GlossarySuggestion.id).where(*clauses).order_by(
            GlossarySuggestion.occurrence_count.desc(), GlossarySuggestion.src_zh))).all()
        for sid in ids:
            by_id.setdefault(sid, None)
    totals = [0, 0, 0]
    new_ids: list[uuid.UUID] = []
    keys = list(by_id)
    for i in range(0, len(keys), ACCEPT_CHUNK):
        a, e, sk, n = await _accept_chunk(session, book_id, {k: by_id[k] for k in keys[i:i + ACCEPT_CHUNK]})
        totals[0] += a
        totals[1] += e
        totals[2] += sk
        new_ids += n
    await events.publish(session, "glossary.suggestions", book_id, {"accepted": totals[0] + totals[1]})
    await session.commit()
    return {"added": totals[0], "existing": totals[1], "skipped": totals[2], "term_ids": [str(t) for t in new_ids]}


async def reject(session: AsyncSession, book_id, ids: list[uuid.UUID], flt=None) -> dict:
    """Mục bị từ chối được nhớ (status = rejected): lần trích sau không đề xuất lại. `flt`: mọi mục pending khớp bộ lọc."""
    await get_book_or_404(session, book_id)
    now = utcnow()
    n = 0
    if ids:
        res = await session.execute(
            update(GlossarySuggestion)
            .where(GlossarySuggestion.book_id == book_id, GlossarySuggestion.id.in_(ids), GlossarySuggestion.status == "pending")
            .values(status="rejected", decided_at=now, updated_at=now).returning(GlossarySuggestion.id))
        n += len(res.all())
    if flt is not None:
        res = await session.execute(update(GlossarySuggestion).where(*_filter_clauses(book_id, flt))
                                    .values(status="rejected", decided_at=now, updated_at=now).returning(GlossarySuggestion.id))
        n += len(res.all())
    await events.publish(session, "glossary.suggestions", book_id, {"rejected": n})
    await session.commit()
    return {"rejected": n}


async def glossary_health(session: AsyncSession, book_id) -> dict:
    """BR-4.14: term có dst_vi còn chữ Hán. BR-4.20: âm Hán Việt nghi ngờ."""
    await get_book_or_404(session, book_id)
    terms = (await session.scalars(select(GlossaryTerm).where(GlossaryTerm.book_id == book_id).order_by(GlossaryTerm.src_zh))).all()
    return {"han_in_dst": [term_view(t) for t in terms if _HAN.search(t.dst_vi)],
            "suspicious_readings": await hanviet.suspicious_readings(session, book_id)}
