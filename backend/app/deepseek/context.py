"""Dựng dữ liệu cho prompt DeepSeek từ DB và file nguồn. Dùng chung cho pool, preview và ước tính."""
import hashlib
import uuid
from dataclasses import dataclass, field

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.glossary import GlossaryIndex
from app.core.hanviet import reading_variants
from app.core.segments import SegmentPlan, plan_segments
from app.core.textio import decode_source
from app.deepseek import prompts
from app.deepseek.glossary_select import PromptTerm, Selection, select_terms
from app.models import DONE_STATUSES, Book, Chapter, ChapterNote, Segment
from app.services import glossary as glossary_service
from app.services import hanviet as hanviet_service
from app.services.paths import chapter_source_path

DEFAULT_MAX_TERMS = 120


def max_terms(run_config: dict) -> int:
    return int((run_config.get("deepseek") or {}).get("glossary_max_terms", DEFAULT_MAX_TERMS))


def book_system_prompt(book: Book, run_config: dict) -> str:
    return prompts.system_prompt(book.foundation_prompt, book.genre, run_config.get("honorific") or {})


async def read_plans(book: Book, chapter: Chapter) -> tuple[list[SegmentPlan], str]:
    path = chapter_source_path(book.slug, chapter.source_file or f"{chapter.no:04d}.txt")
    data = await anyio.to_thread.run_sync(path.read_bytes)
    return plan_segments(decode_source(data).text), hashlib.sha256(data).hexdigest()


def text_lines(plans: list[SegmentPlan]) -> list[tuple[int, str]]:
    return [(p.idx, p.src.strip()) for p in plans if not p.is_meta]


async def previous_tail(session: AsyncSession, chapter: Chapter) -> str | None:
    """BR-8.4: chỉ khi chương no − 1 đã dịch xong; đang chạy song song thì bỏ qua, không chờ."""
    prev = (await session.scalars(
        select(Chapter).where(Chapter.book_id == chapter.book_id, Chapter.no == chapter.no - 1))).first()
    if prev is None or prev.status not in DONE_STATUSES:
        return None
    dsts = (await session.scalars(
        select(Segment.dst).where(Segment.chapter_id == prev.id, Segment.is_meta.is_(False)).order_by(Segment.idx))).all()
    return prompts.tail_context([d for d in dsts if d])


@dataclass
class TranslateContext:
    system: str
    plans: list[SegmentPlan]
    plan_of: dict[int, SegmentPlan]
    lines: list[tuple[int, str]]
    indent: dict[int, str]
    source_hash: str
    index: GlossaryIndex
    glossary: Selection
    context: str | None
    notes: list[str]
    note_ids: list[uuid.UUID]
    title_idx: int | None
    variants: dict[str, tuple[str, ...]] = field(default_factory=dict)  # G4: cách đọc Hán Việt của term trong chương


async def hanviet_variants(session: AsyncSession, index: GlossaryIndex, text: str) -> dict[str, tuple[str, ...]]:
    """G4 (G9): cách đọc Hán Việt tự động của src_zh, dùng làm biến thể khi khác dst_vi (tối đa 64 tổ hợp)."""
    if not index or not text:
        return {}
    hit = {m.term.id: m.term for m in index.find(text)}
    table = await hanviet_service.readings_for(session, {c for t in hit.values() for c in t.src})
    out: dict[str, tuple[str, ...]] = {}
    for tid, t in hit.items():
        vs = tuple(v for v in reading_variants(t.src, table) if v != t.dst)
        if vs:
            out[tid] = vs
    return out


async def load_translate_context(
    session: AsyncSession, book: Book, chapter: Chapter, run_config: dict,
    *, terms: tuple[GlossaryIndex, dict[str, PromptTerm]] | None = None,
) -> TranslateContext:
    plans, source_hash = await read_plans(book, chapter)
    lines = text_lines(plans)
    index, info = terms or await glossary_service.load_prompt_terms(session, book.id)
    selection = select_terms("\n".join(t for _, t in lines), index, info, max_terms(run_config))
    chain = (run_config.get("deepseek") or {}).get("chain_context", True)
    context = await previous_tail(session, chapter) if chain else None
    notes = (await session.scalars(
        select(ChapterNote)
        .where(ChapterNote.chapter_id == chapter.id, ChapterNote.type == "correction", ChapterNote.resolved.is_(False))
        .order_by(ChapterNote.created_at, ChapterNote.id)
    )).all()
    text_plans = [p for p in plans if not p.is_meta]
    first = text_plans[0] if text_plans else None
    title_idx = first.idx if first is not None and first.src.strip() == (chapter.title_zh or "").strip() else None
    variants = await hanviet_variants(session, index, "\n".join(t for _, t in lines))
    return TranslateContext(
        system=book_system_prompt(book, run_config),
        plans=plans,
        plan_of={p.idx: p for p in text_plans},
        lines=lines,
        indent={p.idx: p.src[: len(p.src) - len(p.src.lstrip())] for p in text_plans},
        source_hash=source_hash,
        index=index,
        glossary=selection,
        context=context,
        notes=[n.content for n in notes],
        note_ids=[n.id for n in notes],
        title_idx=title_idx,
        variants=variants,
    )


@dataclass
class ReviewContext:
    items: list[prompts.ReviewItem]
    glossary: Selection


async def load_review_context(session: AsyncSession, book: Book, chapter: Chapter, run_config: dict) -> ReviewContext:
    """BR-8.3b: soát cũng chọn glossary theo 4.1a. Câu sửa tay vẫn gửi nhưng đánh dấu khoá (BR-8.22)."""
    segs = (await session.scalars(
        select(Segment).where(Segment.chapter_id == chapter.id, Segment.is_meta.is_(False)).order_by(Segment.idx))).all()
    items = [prompts.ReviewItem(s.idx, s.src.strip(), s.dst.strip(), s.edited) for s in segs if s.dst and s.dst.strip()]
    index, info = await glossary_service.load_prompt_terms(session, book.id)
    return ReviewContext(items, select_terms("\n".join(it.src for it in items), index, info, max_terms(run_config)))
