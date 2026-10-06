import csv
import io
import json
import logging
import uuid
from collections.abc import Callable

import anyio
from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.glossary import GlossaryIndex, Term, nfc
from app.db import get_sessionmaker
from app.deepseek.glossary_select import PromptTerm
from app.errors import AppError
from app.models import Book, Chapter, GlossaryTerm, Segment, utcnow
from app.schemas import TermCreate, TermUpdate
from app.services import hanviet
from app.services.books import get_book_or_404
from app.services.paths import chapter_source_path

log = logging.getLogger(__name__)
TSV_HEADER = ["src_zh", "dst_vi", "category", "aliases", "enabled"]
DONE = ("translated", "needs_review", "reviewed")


def to_term(t: GlossaryTerm) -> Term:
    return Term(str(t.id), nfc(t.src_zh), nfc(t.dst_vi), tuple(nfc(a) for a in (t.aliases or ())))


async def load_index(session: AsyncSession, book_id: uuid.UUID) -> GlossaryIndex:
    rows = await session.scalars(select(GlossaryTerm).where(GlossaryTerm.book_id == book_id, GlossaryTerm.enabled))
    return GlossaryIndex(to_term(t) for t in rows)


async def load_prompt_terms(session: AsyncSession, book_id: uuid.UUID) -> tuple[GlossaryIndex, dict[str, PromptTerm]]:
    """Term đang bật cho DeepSeek: index để dò (L1, G4) và thông tin dựng dòng (L2). Ghi chú chỉ gửi khi bật prompt_note."""
    rows = (await session.scalars(select(GlossaryTerm).where(GlossaryTerm.book_id == book_id, GlossaryTerm.enabled))).all()
    info = {str(t.id): PromptTerm(str(t.id), t.src_zh, t.dst_vi, t.notes if t.prompt_note else None,
                                  t.predictable, t.always_send) for t in rows}
    return GlossaryIndex(to_term(t) for t in rows), info


def term_view(t: GlossaryTerm) -> dict:
    return {
        "id": str(t.id), "book_id": str(t.book_id), "src_zh": t.src_zh, "dst_vi": t.dst_vi, "category": t.category, "name_lang": t.name_lang,
        "notes": t.notes, "aliases": t.aliases, "enabled": t.enabled, "predictable": t.predictable,
        "always_send": t.always_send, "prompt_note": t.prompt_note, "miss_count": t.miss_count, "occurrence_count": t.occurrence_count,
        "origin": t.origin, "created_at": t.created_at, "updated_at": t.updated_at,
    }


async def get_term_or_404(session: AsyncSession, term_id, *, lock: bool = False) -> GlossaryTerm:
    stmt = select(GlossaryTerm).where(GlossaryTerm.id == term_id)
    if lock:
        stmt = stmt.with_for_update()
    t = (await session.scalars(stmt)).first()
    if t is None:
        raise AppError("TERM_NOT_FOUND", "Không tìm thấy thuật ngữ", 404)
    return t


async def list_terms(session: AsyncSession, book_id, *, q: str | None = None, category: str | None = None,
                     sort: str = "src", send: str = "all") -> list[dict]:
    await get_book_or_404(session, book_id)
    stmt = select(GlossaryTerm).where(GlossaryTerm.book_id == book_id)
    if category:
        stmt = stmt.where(GlossaryTerm.category == category)
    if send == "send":  # BR-4.16: term sẽ gửi cho DeepSeek khi gặp
        stmt = stmt.where(or_(GlossaryTerm.predictable.is_(False), GlossaryTerm.always_send))
    elif send == "predictable":
        stmt = stmt.where(GlossaryTerm.predictable, GlossaryTerm.always_send.is_(False))
    if q and q.strip():
        raw = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(or_(
            GlossaryTerm.src_zh.ilike(f"%{raw}%"),
            func.f_unaccent(func.lower(GlossaryTerm.dst_vi)).ilike(func.concat("%", func.f_unaccent(func.lower(raw)), "%")),
        ))
    order = (GlossaryTerm.occurrence_count.desc(), GlossaryTerm.src_zh) if sort == "occurrence" else (GlossaryTerm.src_zh,)
    return [term_view(t) for t in await session.scalars(stmt.order_by(*order))]


async def summary(session: AsyncSession, book_id) -> dict:
    """BR-4.16: "N term · M term sẽ gửi khi gặp (không tự đoán được hoặc luôn gửi)"."""
    total = await session.scalar(select(func.count()).select_from(GlossaryTerm).where(GlossaryTerm.book_id == book_id))
    will_send = await session.scalar(select(func.count()).select_from(GlossaryTerm).where(
        GlossaryTerm.book_id == book_id, GlossaryTerm.enabled,
        or_(GlossaryTerm.predictable.is_(False), GlossaryTerm.always_send)))
    return {"total": int(total or 0), "will_send": int(will_send or 0)}


async def create_term(session: AsyncSession, book_id, data: TermCreate) -> GlossaryTerm:
    await get_book_or_404(session, book_id)
    term = GlossaryTerm(book_id=book_id, origin="manual", **data.model_dump())
    try:
        session.add(term)
        await hanviet.refresh_terms(session, [term])  # BR-4.15 (tính trước khi học)
        await hanviet.learn_from_terms(session, [term])  # mục 6a bước 4
        await session.commit()
    except IntegrityError as e:
        await session.rollback()
        raise AppError("TERM_DUPLICATE", f"Đã có thuật ngữ “{data.src_zh}” trong truyện này", 409) from e
    await session.refresh(term)
    return term


async def affected_chapter_ids(session: AsyncSession, term: GlossaryTerm) -> list[str]:
    rows = await session.scalars(
        select(Chapter.id).join(Segment, Segment.chapter_id == Chapter.id)
        .where(Chapter.book_id == term.book_id, Chapter.status.in_(DONE), Segment.glossary_hits.contains([str(term.id)]))
        .distinct()
    )
    return [str(r) for r in rows]


async def update_term(session: AsyncSession, term_id, data: TermUpdate) -> dict:
    term = await get_term_or_404(session, term_id, lock=True)
    changes = data.model_dump(exclude_unset=True)
    meaning_changed = ("dst_vi" in changes and changes["dst_vi"] != term.dst_vi) or (
        "aliases" in changes and changes["aliases"] != term.aliases)
    if any(changes.get(k) is not None and changes[k] != getattr(term, k) for k in ("always_send", "dst_vi")):
        term.miss_count = 0  # người dùng quyết định lại: đếm miss từ đầu, nếu không "Luôn gửi" tự bật lại ngay
    for k, v in changes.items():
        if k in ("dst_vi", "enabled", "always_send", "prompt_note", "category") and v is None:
            continue
        setattr(term, k, v)
    if {"dst_vi", "category", "name_lang"} & changes.keys():
        await hanviet.refresh_terms(session, [term])
        await hanviet.learn_from_terms(session, [term])
    term.updated_at = utcnow()
    await session.commit()
    await session.refresh(term)
    affected = await affected_chapter_ids(session, term) if meaning_changed else []
    return {"term": term_view(term), "affected_chapter_ids": affected}


async def delete_term(session: AsyncSession, term_id) -> uuid.UUID:
    term = await get_term_or_404(session, term_id)
    book_id = term.book_id
    await session.delete(term)
    await session.commit()
    return book_id


def _flag(v) -> bool:
    return str(v).strip().lower() not in ("0", "false", "no")


def parse_import(data: bytes, filename: str) -> list[TermCreate]:
    errors: list[dict] = []
    rows: list[tuple[int, dict]] = []
    text = data.decode("utf-8-sig", errors="replace")
    if filename.lower().endswith(".json") or text.lstrip().startswith(("[", "{")):
        try:
            payload = json.loads(text)
        except ValueError as e:
            raise AppError("IMPORT_INVALID", "File JSON không hợp lệ", 422, {"errors": [{"line": 0, "message": str(e)}]}) from e
        if not isinstance(payload, list):
            raise AppError("IMPORT_INVALID", "File JSON phải là một mảng", 422, {"errors": [{"line": 0, "message": "Cần mảng [...]"}]})
        rows = [(i + 1, r if isinstance(r, dict) else {}) for i, r in enumerate(payload)]
    else:
        reader = csv.reader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
        header = [h.strip() for h in next(reader, [])]
        if "src_zh" not in header or "dst_vi" not in header:
            raise AppError("IMPORT_INVALID", "Thiếu cột src_zh hoặc dst_vi ở dòng đầu", 422,
                           {"errors": [{"line": 1, "message": "Header cần có src_zh và dst_vi"}]})
        for n, cells in enumerate(reader, start=2):
            if not any(c.strip() for c in cells):
                continue
            r = dict(zip(header, cells))
            if "aliases" in r:
                r["aliases"] = [a for a in r["aliases"].split("|")]
            if "enabled" in r:
                r["enabled"] = _flag(r["enabled"]) if r["enabled"].strip() else True
            rows.append((n, r))
    items: list[TermCreate] = []
    seen: set[str] = set()
    for line, r in rows:
        r = {k: v for k, v in r.items() if k in ("src_zh", "dst_vi", "category", "aliases", "enabled", "name_lang", "notes")}
        if not r.get("category"):
            r["category"] = "term"
        try:
            item = TermCreate.model_validate(r)
        except ValidationError as e:
            errors.append({"line": line, "message": "; ".join(err["msg"] for err in e.errors())})
            continue
        if item.src_zh in seen:
            errors.append({"line": line, "message": f"Trùng {item.src_zh} trong file"})
            continue
        seen.add(item.src_zh)
        items.append(item)
    if errors:
        raise AppError("IMPORT_INVALID", f"File có {len(errors)} dòng lỗi", 422, {"errors": errors[:50]})
    return items


async def import_terms(session: AsyncSession, book_id, items: list[TermCreate], *, on_conflict: str,
                       overwrite: set[str]) -> dict:
    await get_book_or_404(session, book_id, lock=True)
    existing = {t.src_zh: t for t in await session.scalars(
        select(GlossaryTerm).where(GlossaryTerm.book_id == book_id, GlossaryTerm.src_zh.in_([i.src_zh for i in items])))}
    conflicts = [{"src_zh": i.src_zh, "current": existing[i.src_zh].dst_vi, "incoming": i.dst_vi}
                 for i in items if i.src_zh in existing]
    if on_conflict == "ask":
        return {"added": 0, "updated": 0, "kept": 0, "conflicts": conflicts}
    added = updated = kept = 0
    touched: list[GlossaryTerm] = []
    for item in items:
        cur = existing.get(item.src_zh)
        if cur is None:
            term = GlossaryTerm(book_id=book_id, origin="import", **item.model_dump())
            session.add(term)
            touched.append(term)
            added += 1
        elif on_conflict == "overwrite" or item.src_zh in overwrite:
            cur.dst_vi, cur.category, cur.aliases, cur.enabled = item.dst_vi, item.category, item.aliases, item.enabled
            cur.updated_at = utcnow()
            touched.append(cur)
            updated += 1
        else:
            kept += 1
    try:
        await hanviet.refresh_terms(session, touched)  # BR-4.15: tính lại khi nhập
        await session.commit()
    except IntegrityError as e:  # có người thêm cùng term giữa lúc đọc và ghi
        await session.rollback()
        raise AppError("TERM_DUPLICATE", "Có thuật ngữ vừa được thêm trùng với file nhập, thử nhập lại", 409) from e
    return {"added": added, "updated": updated, "kept": kept, "conflicts": []}


async def export_terms(session: AsyncSession, book_id, fmt: str) -> tuple[bytes, str, str]:
    book = await get_book_or_404(session, book_id)
    terms = (await session.scalars(select(GlossaryTerm).where(GlossaryTerm.book_id == book_id).order_by(GlossaryTerm.src_zh))).all()
    if fmt == "json":
        body = json.dumps([{"src_zh": t.src_zh, "dst_vi": t.dst_vi, "category": t.category, "aliases": t.aliases,
                            "enabled": t.enabled} for t in terms], ensure_ascii=False, indent=2)
        return body.encode(), "application/json", f"{book.slug}-glossary.json"
    lines = ["\t".join(TSV_HEADER)] + [
        "\t".join([t.src_zh, t.dst_vi, t.category, "|".join(t.aliases), "1" if t.enabled else "0"]) for t in terms
    ]
    return ("\n".join(lines) + "\n").encode(), "text/tab-separated-values; charset=utf-8", f"{book.slug}-glossary.tsv"


async def copy_terms(session: AsyncSession, book_id, from_book_id) -> dict:
    await get_book_or_404(session, book_id, lock=True)
    await get_book_or_404(session, from_book_id)
    have = set(nfc(x) for x in (await session.scalars(select(GlossaryTerm.src_zh).where(GlossaryTerm.book_id == book_id))).all())
    added = skipped = 0
    copied: list[GlossaryTerm] = []
    for t in await session.scalars(select(GlossaryTerm).where(GlossaryTerm.book_id == from_book_id)):
        if nfc(t.src_zh) in have:
            skipped += 1
            continue
        term = GlossaryTerm(book_id=book_id, src_zh=nfc(t.src_zh), dst_vi=nfc(t.dst_vi), category=t.category,
                            name_lang=t.name_lang, notes=t.notes, aliases=[nfc(a) for a in t.aliases], enabled=t.enabled,
                            origin="copied")
        session.add(term)
        copied.append(term)
        added += 1
    await hanviet.refresh_terms(session, copied)
    await session.commit()
    return {"added": added, "skipped": skipped}


async def recount_occurrences(book_id: uuid.UUID) -> None:
    """BR-4.4: đếm lại số lần xuất hiện của mọi term trên toàn bộ bản gốc (chạy nền)."""
    sessions = get_sessionmaker()
    try:
        async with sessions() as s:
            book = await s.get(Book, book_id)
            if book is None:
                return
            terms = (await s.scalars(select(GlossaryTerm).where(GlossaryTerm.book_id == book_id))).all()
            files = [chapter_source_path(book.slug, c.source_file or f"{c.no:04d}.txt")
                     for c in (await s.scalars(select(Chapter).where(Chapter.book_id == book_id))).all()]
        index = GlossaryIndex(to_term(t) for t in terms)

        def count_all() -> dict[str, int]:
            total: dict[str, int] = {}
            for path in files:
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                for k, v in index.count(text).items():
                    total[k] = total.get(k, 0) + v
            return total

        counts = await anyio.to_thread.run_sync(count_all) if index else {}
        async with sessions() as s:
            for t in terms:
                await s.execute(update(GlossaryTerm).where(GlossaryTerm.id == t.id)
                                .values(occurrence_count=counts.get(str(t.id), 0)))
            await s.commit()
    except Exception:
        log.exception("Đếm số lần xuất hiện glossary của truyện %s lỗi", book_id)


def translate_snippet(text: str, factory: Callable) -> str:
    from app.core.lines import has_chinese

    if not has_chinese(text):
        return text
    try:
        out = factory().translate([text], beam=1, batch_size=1).outputs
    except Exception as e:
        raise AppError("TRANSLATOR_UNAVAILABLE", "Chưa dùng được model dịch", 503) from e
    return out[0].strip() if out else ""