"""Bảng âm Hán Việt (spec 04 mục 6a, BR-4.15 → BR-4.20): tra, sửa tay, nạp seed, học từ glossary, bổ sung nền,
tính `predictable` cho term.

Không import app.services.ai_cost ở đầu file: ai_cost → deepseek.context → services.glossary → module này."""
import asyncio
import hashlib
import logging
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path

import anyio
from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ROOT
from app.core.hanviet import check_predictable, han_chars, is_han, learn_pairs, normalize_reading, syllable_key
from app.db import get_sessionmaker
from app.deepseek import hanviet_ai
from app.deepseek.client import DeepSeekAuthError, DeepSeekClient, DeepSeekError, OutputRejected, Usage
from app.errors import AppError
from app.models import AppMeta, Book, Chapter, GlossaryTerm, HanvietReading, HanvietUnknown, WorkerState, utcnow
from app.services import logs
from app.services.paths import chapter_source_path

log = logging.getLogger(__name__)
SEED_PATH = ROOT / "data" / "hanviet_seed.tsv"
SEED_HEADER = ["char", "reading", "source", "confidence"]
CONFIDENCE = {"ai": 60, "confirmed": 90, "learned": 85, "manual": 100}
SEED_META_KEY = "hanviet_seed_sha256"
MAX_AUTOFILL_CHARS = 300  # số chữ tối đa mỗi lượt bổ sung nền (lượt sau hỏi tiếp phần còn lại)
_warned_no_seed = False
CHUNK = 2000  # asyncpg giới hạn 32.767 tham số mỗi câu lệnh
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def _chunks(items: list, size: int = CHUNK):
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def readings_for(session: AsyncSession, chars: Iterable[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for part in _chunks(sorted(set(chars))):
        rows = await session.execute(
            select(HanvietReading.char, HanvietReading.reading).where(HanvietReading.char.in_(part))
            .order_by(HanvietReading.char, HanvietReading.confidence.desc(), HanvietReading.reading))
        for c, r in rows.all():
            out[c].append(r)
    return dict(out)


async def known_chars(session: AsyncSession, chars: Iterable[str]) -> set[str]:
    """Chữ đã có âm, hoặc đã hỏi mà DeepSeek trả rỗng."""
    have: set[str] = set()
    for part in _chunks(sorted(set(chars))):
        have.update((await session.scalars(select(HanvietReading.char).where(HanvietReading.char.in_(part)).distinct())).all())
        have.update((await session.scalars(select(HanvietUnknown.char).where(HanvietUnknown.char.in_(part)))).all())
    return have


async def _insert(session: AsyncSession, pairs: list[tuple[str, str]], source: str) -> int:
    now = utcnow()
    rows = [{"char": c, "reading": r, "source": source, "confidence": CONFIDENCE[source], "created_at": now} for c, r in pairs]
    added = 0
    for part in _chunks(rows):
        res = await session.execute(pg_insert(HanvietReading).values(part).on_conflict_do_nothing().returning(HanvietReading.char))
        added += len(res.all())
    return added


async def add_readings(session: AsyncSession, pairs: Iterable[tuple[str, str]], source: str) -> int:
    """Thêm âm đã chuẩn hoá. Bỏ âm trùng (theo syllable_key) với âm chữ đó đã có."""
    clean = [(c, n) for c, r in pairs if is_han(c) and (n := normalize_reading(r))]
    if not clean:
        return 0
    keys = {c: {syllable_key(x) for x in rs} for c, rs in (await readings_for(session, {c for c, _ in clean})).items()}
    fresh: list[tuple[str, str]] = []
    for c, r in clean:
        k = syllable_key(r)
        if k not in keys.setdefault(c, set()):
            keys[c].add(k)
            fresh.append((c, r))
    return await _insert(session, fresh, source)


# ---------- predictable (BR-4.15) ----------

async def refresh_terms(session: AsyncSession, terms: list[GlossaryTerm]) -> set[str]:
    """Gán predictable cho các term (chưa commit). Trả các chữ còn thiếu âm."""
    if not terms:
        return set()
    table = await readings_for(session, {c for t in terms for c in (han_chars(t.src_zh) or [])})
    missing: set[str] = set()
    for t in terms:
        ok, miss = check_predictable(t.src_zh, t.dst_vi, t.category, t.name_lang, table)
        t.predictable = ok
        missing |= miss
    return missing


async def refresh_predictable(session: AsyncSession, book_id, term_ids: list | None = None) -> set[str]:
    stmt = select(GlossaryTerm).where(GlossaryTerm.book_id == book_id)
    if term_ids is not None:
        stmt = stmt.where(GlossaryTerm.id.in_(term_ids))
    return await refresh_terms(session, list((await session.scalars(stmt)).all()))


async def refresh_for_chars(session: AsyncSession, chars: Iterable[str]) -> int:
    """Tính lại mọi term (mọi truyện) có chứa một trong các chữ này."""
    wanted = [c for c in set(chars) if is_han(c)]
    if not wanted:
        return 0
    terms = list((await session.scalars(select(GlossaryTerm).where(or_(*(GlossaryTerm.src_zh.contains(c) for c in wanted))))).all())
    await refresh_terms(session, terms)
    return len(terms)


async def refresh_all(session: AsyncSession) -> int:
    terms = list((await session.scalars(select(GlossaryTerm))).all())
    await refresh_terms(session, terms)
    return len(terms)


# ---------- học từ glossary (bước 4), âm nghi ngờ (BR-4.20) ----------

async def learn_from_terms(session: AsyncSession, terms: list[GlossaryTerm]) -> int:
    """Chỉ thêm âm `learned` cho chữ CHƯA có âm nào. Chữ đã có âm khác thì để mục "Âm Hán Việt nghi ngờ"."""
    pairs = [p for t in terms for p in learn_pairs(t.src_zh, t.dst_vi, t.category, t.name_lang)]
    if not pairs:
        return 0
    have = await readings_for(session, {c for c, _ in pairs})
    return await add_readings(session, [(c, r) for c, r in pairs if c not in have], "learned")


async def suspicious_readings(session: AsyncSession, book_id) -> list[dict]:
    """Chữ chỉ có âm nguồn `ai` mà một term của truyện đọc khác (BR-4.20)."""
    terms = (await session.scalars(select(GlossaryTerm).where(GlossaryTerm.book_id == book_id).order_by(GlossaryTerm.src_zh))).all()
    pairs = [(t, learn_pairs(t.src_zh, t.dst_vi, t.category, t.name_lang)) for t in terms]
    chars = {c for _, ps in pairs for c, _ in ps}
    rows: dict[str, list[HanvietReading]] = defaultdict(list)
    for part in _chunks(sorted(chars)):
        for r in await session.scalars(select(HanvietReading).where(HanvietReading.char.in_(part))
                                       .order_by(HanvietReading.confidence.desc(), HanvietReading.reading)):
            rows[r.char].append(r)
    out: dict[str, dict] = {}
    for t, ps in pairs:
        for c, reading in ps:
            rs = rows.get(c)
            if not rs or any(x.source != "ai" for x in rs) or syllable_key(reading) in {syllable_key(x.reading) for x in rs}:
                continue
            entry = out.setdefault(c, {"char": c, "readings": [x.reading for x in rs], "proposed": reading, "terms": []})
            entry["terms"].append({"id": str(t.id), "src_zh": t.src_zh, "dst_vi": t.dst_vi})
    return [out[c] for c in sorted(out)]


# ---------- tra / sửa tay ----------

async def lookup(session: AsyncSession, chars: str) -> list[dict]:
    wanted = list(dict.fromkeys(c for c in chars if is_han(c)))[:200]
    by: dict[str, list[dict]] = defaultdict(list)
    if wanted:
        for r in await session.scalars(select(HanvietReading).where(HanvietReading.char.in_(wanted))
                                       .order_by(HanvietReading.char, HanvietReading.confidence.desc(), HanvietReading.reading)):
            by[r.char].append({"reading": r.reading, "source": r.source, "confidence": r.confidence})
    return [{"char": c, "readings": by.get(c, [])} for c in wanted]


async def set_char(session: AsyncSession, char: str, readings: list[str]) -> dict:
    """BR-4.20: người dùng sửa âm một chữ → thay toàn bộ âm của chữ đó bằng nguồn `manual`."""
    if not is_han(char):
        raise AppError("INVALID_CHAR", "Chỉ sửa được âm của một chữ Hán", 422)
    clean: list[str] = []
    for raw in readings:
        r = normalize_reading(raw)
        if r is None:
            raise AppError("INVALID_READING", f"Âm “{raw}” không hợp lệ: cần đúng một âm tiết tiếng Việt", 422)
        clean.append(r)
    await session.execute(delete(HanvietReading).where(HanvietReading.char == char))
    await session.execute(delete(HanvietUnknown).where(HanvietUnknown.char == char))
    await add_readings(session, [(char, r) for r in clean], "manual")
    await refresh_for_chars(session, {char})
    await session.commit()
    return (await lookup(session, char))[0]


# ---------- seed (bước 1–2) ----------

def parse_seed(text: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    for n, line in enumerate(text.splitlines()):
        cells = line.split("\t")
        if (n == 0 and cells[:1] == ["char"]) or len(cells) < 3:
            continue
        c, r, src = cells[0], normalize_reading(cells[1]), cells[2].strip()
        if is_han(c) and r and src in CONFIDENCE:
            rows.append((c, r, src))
    return rows


async def _meta_get(session: AsyncSession, key: str) -> str | None:
    return await session.scalar(select(AppMeta.value).where(AppMeta.key == key))


async def _meta_set(session: AsyncSession, key: str, value: str) -> None:
    stmt = pg_insert(AppMeta).values(key=key, value=value)
    await session.execute(stmt.on_conflict_do_update(index_elements=[AppMeta.key], set_={"value": value, "updated_at": utcnow()}))


async def seed_loaded(session: AsyncSession) -> bool:
    """Đã từng nạp một file seed (có dấu sha256 trong app_meta)."""
    return await _meta_get(session, SEED_META_KEY) is not None


async def _apply_seed(session: AsyncSession, rows: list[tuple[str, str, str]]) -> tuple[int, int]:
    """Seed thắng âm `ai` (bổ sung nền) của cùng chữ; không bao giờ đụng chữ đã có âm manual / confirmed / learned."""
    wanted: dict[str, set[str]] = defaultdict(set)
    for c, r, _src in rows:
        wanted[c].add(r)
    existing: dict[str, list[HanvietReading]] = defaultdict(list)
    for part in _chunks(sorted(wanted)):
        for row in await session.scalars(select(HanvietReading).where(HanvietReading.char.in_(part))):
            existing[row.char].append(row)
    locked = {c for c, rs in existing.items() if any(r.source != "ai" for r in rs)}
    deleted = 0
    for c, rs in existing.items():
        for r in rs:
            if c not in locked and r.reading not in wanted[c]:
                await session.delete(r)  # âm ai khác seed: seed thắng
                deleted += 1
    by_source: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for c, r, src in rows:
        if c not in locked:
            by_source[src].append((c, r))
    await session.flush()
    added = 0
    for src in ("manual", "confirmed", "learned", "ai"):  # nguồn tin cậy cao trước
        added += await _insert(session, by_source[src], src)
    return added, deleted


async def ensure_seed(session: AsyncSession, path: Path = SEED_PATH) -> int:
    """Nạp file seed khi sha256 của file khác dấu đã ghi (app_meta). Gọi lúc API khởi động. Bổ sung nền bằng DeepSeek
    trước khi nạp seed không chặn được seed nữa."""
    if not path.exists():
        return 0
    data = await anyio.to_thread.run_sync(path.read_bytes)
    digest = hashlib.sha256(data).hexdigest()
    if await _meta_get(session, SEED_META_KEY) == digest:
        return 0
    rows = await anyio.to_thread.run_sync(lambda: parse_seed(data.decode("utf-8")))
    added, deleted = await _apply_seed(session, rows)
    await _meta_set(session, SEED_META_KEY, digest)
    if added or deleted:
        await refresh_all(session)
    return added


# ---------- bổ sung nền (bước 3, AC-4.18) ----------

def _source_chars(paths: list[Path]) -> set[str]:
    out: set[str] = set()
    for p in paths:
        try:
            out.update(_HAN.findall(p.read_text(encoding="utf-8")))
        except OSError:
            continue
    return out


_fill_locks: dict[uuid.UUID, asyncio.Lock] = {}


async def fill_missing(book_id: uuid.UUID, client_factory: Callable[[], DeepSeekClient]) -> int:
    """Gom chữ của truyện (bản gốc + glossary) chưa có âm, hỏi DeepSeek theo lô 400 chữ (tối đa MAX_AUTOFILL_CHARS chữ
    mỗi lượt), rồi tính lại predictable. Chạy nền sau khi tạo truyện / thêm chương / thêm term. Không bao giờ ném lỗi.
    Bỏ qua khi engine DeepSeek đang tạm dừng hoặc khi chưa nạp seed (`make hanviet`). Mỗi truyện chỉ chạy một lượt."""
    lock = _fill_locks.setdefault(book_id, asyncio.Lock())
    try:
        async with lock:
            return await _fill_missing(book_id, client_factory)
    except Exception:
        log.exception("Bổ sung âm Hán Việt cho truyện %s lỗi", book_id)
        return 0


async def _fill_missing(book_id: uuid.UUID, client_factory: Callable[[], DeepSeekClient]) -> int:
    global _warned_no_seed
    from app.services import ai_cost  # import muộn: xem docstring module

    sessions = get_sessionmaker()
    async with sessions() as s:
        book = await s.get(Book, book_id)
        if book is None:
            return 0
        if await s.scalar(select(WorkerState.paused).where(WorkerState.engine == "deepseek")):
            return 0  # engine DeepSeek đang tạm dừng: không gọi API
        if not await seed_loaded(s):
            if not _warned_no_seed:
                _warned_no_seed = True
                log.warning("Chưa nạp seed âm Hán Việt: không bổ sung âm bằng DeepSeek. Chạy `make hanviet` rồi khởi động lại app.")
            return 0
        rows = (await s.execute(select(Chapter.no, Chapter.source_file).where(Chapter.book_id == book_id))).all()
        paths = [chapter_source_path(book.slug, f or f"{no:04d}.txt") for no, f in rows]
        srcs = (await s.scalars(select(GlossaryTerm.src_zh).where(GlossaryTerm.book_id == book_id))).all()
    chars = await anyio.to_thread.run_sync(_source_chars, paths)
    chars |= {c for src in srcs for c in _HAN.findall(src)}
    async with sessions() as s:
        every_missing = sorted(chars - await known_chars(s, chars))
    if not every_missing:
        return 0
    missing = every_missing[:MAX_AUTOFILL_CHARS]
    overflow = len(every_missing) - len(missing)
    if overflow:
        log.warning("Bổ sung âm Hán Việt truyện %s: %d chữ vượt giới hạn %d chữ mỗi lượt, để lượt sau",
                    book_id, overflow, MAX_AUTOFILL_CHARS)
    added, asked, usage, error = 0, 0, Usage(), None
    t0 = time.perf_counter()
    try:
        client = client_factory()
        for part in hanviet_ai.batches(missing):
            found, completion = await hanviet_ai.ask_readings(client, part)
            usage, asked = usage + completion.usage, asked + len(part)
            async with sessions() as s:
                added += await add_readings(s, [(c, r) for c, rs in found.items() for r in rs], "ai")
                empty = [{"char": c, "created_at": utcnow()} for c in part if not found.get(c)]
                if empty:
                    await s.execute(pg_insert(HanvietUnknown).values(empty).on_conflict_do_nothing())
                await s.commit()
    except (DeepSeekError, OutputRejected) as e:
        error = e
    async with sessions() as s:
        model = await ai_cost.get_model(s, hanviet_ai.MODEL)
        if isinstance(error, DeepSeekAuthError) and error.status is None:
            level, message = "info", f"Chưa có DEEPSEEK_API_KEY: chưa bổ sung âm Hán Việt cho {len(missing)} chữ"
        elif error is not None:
            level, message = "warn", f"Bổ sung âm Hán Việt dừng sau {asked}/{len(missing)} chữ: {error}"
        else:
            level = "info"
            message = (f"Bổ sung âm Hán Việt cho {len(missing)} chữ ({added} âm mới) · "
                       f"{usage.prompt_tokens + usage.completion_tokens} token")
        if overflow:
            level = "warn"
            message += f" · còn {overflow} chữ vượt giới hạn {MAX_AUTOFILL_CHARS} chữ mỗi lượt, bổ sung ở lượt sau"
        spent = usage.prompt_tokens > 0
        await logs.write_log(
            s, level=level, source="glossary", book_id=book_id, provider="deepseek", model=hanviet_ai.MODEL,
            message=message, tokens_in=usage.prompt_tokens if spent else None,
            tokens_out=usage.completion_tokens if spent else None,
            tokens_in_cached=usage.prompt_cache_hit_tokens if spent else None,
            cost_usd=ai_cost.cost_for(model, usage) if spent else None,
            latency_ms=int((time.perf_counter() - t0) * 1000),
            detail={"chars": len(missing), "asked": asked, "added": added, "overflow": overflow},
        )
        if added:
            await refresh_predictable(s, book_id)
        await s.commit()
    return added
