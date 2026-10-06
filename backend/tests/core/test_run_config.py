import pytest
from pydantic import ValidationError

from app.core.run_config import build_run_config, default_run_config


def test_honorific_defaults_by_genre():
    # AC-2.5
    assert default_run_config("modern_war")["honorific"] == {"kinship": False, "pronoun": False, "modern_stable": True}
    assert default_run_config("xianxia")["honorific"] == {"kinship": True, "pronoun": True, "modern_stable": False}
    assert default_run_config("other")["honorific"] == {"kinship": False, "pronoun": False, "modern_stable": False}


def test_base_defaults():
    cfg = default_run_config("urban")
    assert (cfg["engine"], cfg["model_id"], cfg["beam"], cfg["chunk_mode"]) == ("ct2", "HachimiMT-60", 2, "paragraph")
    assert cfg["han_normalize"] == "auto"
    assert cfg["review"]["auto_after_ct2"] is False
    assert cfg["batch"] == {"auto": True, "size": 32}


def test_override_merges_nested_keys():
    cfg = build_run_config("xianxia", {"beam": 4, "honorific": {"pronoun": False}})
    assert cfg["beam"] == 4
    assert cfg["honorific"] == {"kinship": True, "pronoun": False, "modern_stable": False}


def test_deepseek_engine_accepted():
    cfg = build_run_config("other", {"engine": "deepseek", "model_id": "deepseek-flash"})
    assert cfg["engine"] == "deepseek"


@pytest.mark.parametrize(
    "override",
    [{"beam": 9}, {"foo": 1}, {"engine": "ct2", "model_id": "deepseek-flash"}, {"chunk_mode": "line"}],
)
def test_invalid_overrides_rejected(override):
    with pytest.raises(ValidationError):
        build_run_config("other", override)


def test_ct2_thread_settings_from_env(monkeypatch):
    from app.config import Settings

    assert Settings(_env_file=None).ct2_intra_threads == 8
    monkeypatch.setenv("CT2_INTRA_THREADS", "6")
    monkeypatch.setenv("CT2_INTER_THREADS", "2")
    s = Settings(_env_file=None)
    assert (s.ct2_intra_threads, s.ct2_inter_threads) == (6, 2)
