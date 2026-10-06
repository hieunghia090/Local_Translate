import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from app.db import get_engine
from app.models import Chapter
from app.services.chapters import title_vi_search_ilike

pytestmark = pytest.mark.db


def test_compiled_expression_has_literal_empty_string():
    sql = str(select(Chapter.id).where(title_vi_search_ilike("abc")).compile(dialect=postgresql.dialect()))
    assert "coalesce(chapters.title_vi, '')" in sql


async def test_search_query_uses_trigram_index(migrated_db):
    stmt = select(Chapter.id).where(title_vi_search_ilike("abc"))
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    async with get_engine().begin() as conn:
        await conn.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(r[0] for r in await conn.execute(text("EXPLAIN " + sql)))
    assert "ix_chapters_search_title_vi" in plan, plan
