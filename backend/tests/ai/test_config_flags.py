import pytest
from pydantic import ValidationError

from app.core.jobs import chapter_status_after
from app.core.run_config import build_run_config, deepseek_model, with_engine
from app.deepseek.catalog import is_deepseek_model


def test_deepseek_options_defaults_and_sync():
    cfg = build_run_config("xianxia", {"engine": "deepseek", "model_id": "deepseek-flash"})
    assert cfg["deepseek"]["model_id"] == "deepseek-flash"  # đồng bộ với model_id khi engine = deepseek
    assert cfg["deepseek"]["glossary_max_terms"] == 120 and cfg["deepseek"]["concurrency"] == 3
    assert deepseek_model(cfg) == "deepseek-flash"


def test_concurrency_between_1_and_8():
    with pytest.raises(ValidationError):
        build_run_config("other", {"deepseek": {"concurrency": 9}})
    with pytest.raises(ValidationError):
        build_run_config("other", {"deepseek": {"concurrency": 0}})


def test_with_engine_switches_model():
    base = build_run_config("other", {})
    ds = with_engine(base, "deepseek")
    assert (ds["engine"], ds["model_id"]) == ("deepseek", "deepseek-v4-pro")
    assert (base["engine"], base["model_id"]) == ("ct2", "HachimiMT-60")  # không sửa dict gốc
    back = with_engine(ds, "ct2")
    assert (back["engine"], back["model_id"]) == ("ct2", "HachimiMT-60")
    assert deepseek_model(base) == "deepseek-v4-pro"


def test_deepseek_risk_flags():
    assert chapter_status_after([["fallback_ct2"]]) == "needs_review"
    assert chapter_status_after([["residual_han"]]) == "needs_review"
    assert chapter_status_after([["glossary_miss"]]) == "needs_review"
    assert chapter_status_after([["glossary_autofixed"], ["review_pending"]]) == "translated"


def test_is_deepseek_model():
    assert is_deepseek_model("deepseek-flash") and not is_deepseek_model("HachimiMT-60") and not is_deepseek_model(None)
