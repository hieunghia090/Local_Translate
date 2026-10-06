"""Bản dịch theo engine (spec 06 mục 4a): đọc bản cũ trước khi ghi lại chương; đếm câu khác nhau."""
import uuid

from sqlalchemy import and_, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.compare import WS_PATTERN
from app.models import Segment


async def previous_versions(session: AsyncSession, chapter_id: uuid.UUID) -> dict[int, tuple[str, str | None, str | None]]:
    """(src, dst_mt, dst_ai) theo idx. Gọi TRƯỚC khi pipeline xoá segment của chương (BR-6.10)."""
    rows = await session.execute(
        select(Segment.idx, Segment.src, Segment.dst_mt, Segment.dst_ai).where(Segment.chapter_id == chapter_id))
    return {idx: (src, mt, ai) for idx, src, mt, ai in rows}


EMPTY_COMPARE = {"mt": 0, "ai": 0, "both": 0, "differ": 0}


def compare_summary(rows: list[dict]) -> dict:
    """Từ các dòng segment_view của một chương (đã có mt_ai_differ)."""
    text = [r for r in rows if not r["is_meta"]]
    return {
        "mt": sum(r["dst_mt"] is not None for r in text),
        "ai": sum(r["dst_ai"] is not None for r in text),
        "both": sum(r["dst_mt"] is not None and r["dst_ai"] is not None for r in text),
        "differ": sum(r["mt_ai_differ"] is True for r in text),
    }


def _norm_sql(col):
    """Giống app.core.compare.norm_cmp: NFC, gộp khoảng trắng WS_CHARS, bỏ dấu cách hai đầu (BR-6.11)."""
    return func.btrim(func.regexp_replace(func.normalize(col, literal_column("NFC")), WS_PATTERN, " ", "g"), " ")


async def compare_counts(session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    if not ids:
        return {}
    both = and_(Segment.dst_mt.is_not(None), Segment.dst_ai.is_not(None))
    stmt = (
        select(Segment.chapter_id, func.count(Segment.dst_mt), func.count(Segment.dst_ai), func.count().filter(both),
               func.count().filter(and_(both, _norm_sql(Segment.dst_mt) != _norm_sql(Segment.dst_ai))))
        .where(Segment.chapter_id.in_(ids), Segment.is_meta.is_(False))
        .group_by(Segment.chapter_id)
    )
    return {cid: {"mt": mt, "ai": ai, "both": b, "differ": d} for cid, mt, ai, b, d in await session.execute(stmt)}
