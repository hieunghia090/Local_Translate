import os
from pathlib import Path

import pytest
from sqlalchemy import insert, text

# Engine không giữ pool, để kết nối không bị gắn vào event loop của test trước.
os.environ.setdefault("LT_DB_NULLPOOL", "1")

BACKEND = Path(__file__).resolve().parents[1]


def alembic_config():
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return cfg


@pytest.fixture(scope="session")
def migrated_db():
    from alembic import command

    from app import db
    from app.config import get_settings

    url = get_settings().test_database_url
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    db.reset_engine()
    command.upgrade(alembic_config(), "head")
    yield url


@pytest.fixture
async def clean_db(migrated_db):
    from app import db
    from app.deepseek.catalog import DEFAULT_MODELS
    from app.models import AiModel

    async with db.get_engine().begin() as conn:
        await conn.execute(text("TRUNCATE books, import_sessions, log_entries, hanviet_readings, hanviet_unknown, app_meta CASCADE"))
        await conn.execute(text("UPDATE worker_state SET paused = false, paused_reason = NULL"))
        await conn.execute(text("DELETE FROM ai_models"))  # test có thể sửa giá: nạp lại giá mẫu
        await conn.execute(insert(AiModel), DEFAULT_MODELS)
    yield


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from app.config import get_settings

    root = tmp_path / "lt"
    monkeypatch.setenv("DATA_DIR", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()

@pytest.fixture
def fake_translator():
    from app.core.translator import FakeTranslator

    return FakeTranslator()


@pytest.fixture
def fake_deepseek():
    from fake_deepseek import FakeDeepSeek

    return FakeDeepSeek()


@pytest.fixture(autouse=True)
def _no_real_deepseek(request, monkeypatch):
    """Chỉ test @live chạy với LT_LIVE=1 mới được dựng client DeepSeek bằng key thật trong .env."""
    if request.node.get_closest_marker("live") and os.environ.get("LT_LIVE") == "1":
        return
    from app.deepseek.client import DeepSeekClient

    def forbidden(cls):
        raise AssertionError("Test không được dùng DeepSeekClient.from_settings (key thật); dùng FakeDeepSeek")

    monkeypatch.setattr(DeepSeekClient, "from_settings", classmethod(forbidden))


@pytest.fixture
async def api(clean_db, data_dir, fake_translator, fake_deepseek):
    import httpx

    from app.main import create_app

    # Không bao giờ dùng key thật trong .env khi test
    app = create_app(translator_factory=lambda: fake_translator, deepseek_client_factory=fake_deepseek.client,
                     hanviet_autofill=False)  # test nào cần bổ sung nền thì tự dựng app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client
