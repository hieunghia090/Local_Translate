import pytest

from app.config import get_settings
from app.devtools import seed_compare


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/main")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/main_test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_refuses_main_db_with_nonzero_exit(env, capsys):
    with pytest.raises(SystemExit) as e:
        seed_compare.main([])
    err = capsys.readouterr().err
    assert e.value.code != 0 and "--yes-main-db" in err and "1 truyện" in err


def test_allows_test_db(env, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:5432/main_test")
    get_settings.cache_clear()
    assert seed_compare.guard_target(False) is None
