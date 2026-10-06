from pathlib import Path

from app.config import Settings


def make(**env) -> Settings:
    return Settings(_env_file=None, **env)


def test_async_url_converts_scheme():
    s = make(database_url="postgresql://u:p@127.0.0.1:5432/local_translate")
    assert s.async_database_url == "postgresql+asyncpg://u:p@127.0.0.1:5432/local_translate"


def test_async_url_keeps_asyncpg_scheme():
    s = make(database_url="postgresql+asyncpg://u:p@h/db")
    assert s.async_database_url == "postgresql+asyncpg://u:p@h/db"


def test_test_database_url_derived_from_main_db():
    s = make(database_url="postgresql://u:p@127.0.0.1:5432/local_translate")
    assert s.test_database_url == "postgresql://u:p@127.0.0.1:5432/local_translate_test"


def test_test_database_url_explicit_wins():
    s = make(database_url="postgresql://u:p@h/a", test_database_url_override="postgresql://u:p@h/b")
    assert s.test_database_url == "postgresql://u:p@h/b"


def test_masked_key_shows_last_four_only():
    assert make(deepseek_api_key="sk-abcdef123456").masked_deepseek_key() == "••••3456"
    assert make(deepseek_api_key="").masked_deepseek_key() is None


def test_data_path_expands_home():
    s = make(data_dir="~/LocalTranslate")
    assert s.data_path == Path.home() / "LocalTranslate"


def test_defaults_bind_localhost():
    s = make()
    assert s.app_host == "127.0.0.1"
    assert s.deepseek_base_url == "https://api.deepseek.com"
