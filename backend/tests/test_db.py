import pytest
from sqlalchemy import text

from app import db

pytestmark = pytest.mark.db


async def test_ping_ok(migrated_db):
    assert await db.ping() is True


async def test_extensions_installed(migrated_db):
    async with db.get_engine().connect() as conn:
        rows = (await conn.execute(text("SELECT extname FROM pg_extension"))).scalars().all()
    assert {"unaccent", "pg_trgm"} <= set(rows)


async def test_ping_false_when_unreachable(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:1/nope")
    from app.config import get_settings

    get_settings.cache_clear()
    db.reset_engine()
    try:
        assert await db.ping() is False
    finally:
        get_settings.cache_clear()
        db.reset_engine()
