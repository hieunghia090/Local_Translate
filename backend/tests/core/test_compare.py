import pytest

from app.core.compare import AI_COLUMN, MT_COLUMN, carried_versions, engine_column, norm_cmp, versions_differ
from core.compare_cases import CASES


@pytest.mark.parametrize("mt, ai, expected", CASES)
def test_versions_differ_cases(mt, ai, expected):
    assert versions_differ(mt, ai) is expected


def test_missing_side_is_none():
    assert versions_differ(None, "a") is None and versions_differ("a", None) is None and versions_differ(None, None) is None


def test_norm_cmp():
    assert norm_cmp("  Hắn  \t đi.\n") == "Hắn đi."
    assert norm_cmp("") == ""


def test_engine_column():
    assert engine_column("deepseek-v4-pro") == engine_column("deepseek-flash") == AI_COLUMN
    assert engine_column("HachimiMT-60") == engine_column("fake") == engine_column(None) == MT_COLUMN


def test_carried_versions_keeps_other_engine_only_when_src_same():
    prev = ("他走了。", "MT cũ", "AI cũ")
    assert carried_versions(MT_COLUMN, "他走了。", "MT mới", prev) == {"dst_mt": "MT mới", "dst_ai": "AI cũ"}
    assert carried_versions(AI_COLUMN, "他走了。", "AI mới", prev) == {"dst_ai": "AI mới", "dst_mt": "MT cũ"}
    assert carried_versions(MT_COLUMN, "她走了。", "MT mới", prev) == {"dst_mt": "MT mới", "dst_ai": None}
    assert carried_versions(AI_COLUMN, "他走了。", "AI mới", None) == {"dst_ai": "AI mới", "dst_mt": None}
