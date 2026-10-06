"""Prompt nền của truyện (spec 08 mục 3) và preview prompt đầy đủ cho một chương."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.run_config import deep_merge, deepseek_model
from app.core.textio import SourceDecodeError, UnsupportedEncodingError
from app.deepseek import prompts
from app.deepseek.context import load_translate_context
from app.deepseek.estimate import prompt_tokens_est
from app.errors import AppError
from app.models import Book, Chapter, utcnow
from app.services.ai_cost import load_coefficients
from app.services.books import get_book_or_404


def _view(book: Book) -> dict:
    return {"foundation_prompt": book.foundation_prompt or prompts.DEFAULT_FOUNDATION,
            "is_default": book.foundation_prompt is None,
            "honorific_block": prompts.honorific_block(book.genre, (book.run_config or {}).get("honorific") or {})}


async def get_foundation(session: AsyncSession, book_id) -> dict:
    return _view(await get_book_or_404(session, book_id))


async def set_foundation(session: AsyncSession, book_id, text: str | None) -> dict:
    book = await get_book_or_404(session, book_id, lock=True)
    text = (text or "").strip()
    book.foundation_prompt = None if not text or text == prompts.DEFAULT_FOUNDATION.strip() else text
    book.updated_at = utcnow()
    await session.commit()
    return _view(book)


async def preview(session: AsyncSession, book_id, chapter_no: int) -> dict:
    book = await get_book_or_404(session, book_id)
    ch = (await session.scalars(select(Chapter).where(Chapter.book_id == book.id, Chapter.no == chapter_no))).first()
    if ch is None:
        raise AppError("CHAPTER_NOT_FOUND", "Không tìm thấy chương", 404)
    cfg = deep_merge(book.run_config, ch.run_config_override or {})
    try:
        tc = await load_translate_context(session, book, ch, cfg)
    except (OSError, SourceDecodeError, UnsupportedEncodingError) as e:
        raise AppError("SOURCE_MISSING", "Không đọc được file bản gốc của chương", 409) from e
    user = prompts.user_prompt(glossary_lines=tc.glossary.lines, context=tc.context, notes=tc.notes, lines=tc.lines)
    model_id = deepseek_model(cfg)
    coeff = await load_coefficients(session, book.id, model_id)
    return {"chapter_no": ch.no, "model_id": model_id, "system": tc.system, "user": user,
            "glossary": tc.glossary.stats(), "tokens_in_est": prompt_tokens_est(tc.system, user, coeff)}
