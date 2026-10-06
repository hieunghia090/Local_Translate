import unicodedata

import pytest

from app.core.hanviet import (
    check_predictable, han_chars, learn_pairs, normalize_reading, reading_variants, syllable_key, syllables,
)

R = {
    "林": ["lâm"], "凡": ["phàm"], "赵": ["triệu"], "楷": ["khải", "giai"], "宗": ["tông"], "门": ["môn"],
    "大": ["đại"], "比": ["tỷ", "bỉ"], "和": ["hòa"], "贵": ["quý"], "赫": ["hách"], "鲁": ["lỗ"], "晓": ["hiểu"],
    "夫": ["phu"], "田": ["điền"], "中": ["trung"], "一": ["nhất"], "郎": ["lang"],
}


def test_ac_4_14_lam_pham_then_lam_phan():
    assert check_predictable("林凡", "Lâm Phàm", "character", None, R) == (True, frozenset())
    assert check_predictable("林凡", "Lâm Phàn", "character", None, R) == (False, frozenset())


def test_ac_4_15_foreign_and_japanese_never_predictable():
    assert check_predictable("赫鲁晓夫", "Hách Lỗ Hiểu Phu", "character", "foreign", R)[0] is False
    assert check_predictable("赫鲁晓夫", "Khrushchev", "character", None, R)[0] is False
    assert check_predictable("田中一郎", "Điền Trung Nhất Lang", "character", "ja", R)[0] is False


def test_only_four_categories():
    assert check_predictable("宗门大比", "Tông môn đại tỷ", "organization", None, R)[0] is True
    assert check_predictable("宗门大比", "Tông môn đại tỷ", "term", None, R)[0] is False


def test_missing_chars_reported_only_for_eligible_terms():
    table = {k: v for k, v in R.items() if k != "楷"}
    assert check_predictable("赵楷", "Triệu Khải", "character", None, table) == (False, frozenset({"楷"}))
    assert check_predictable("赵楷", "Triệu Khải", "term", None, table) == (False, frozenset())


@pytest.mark.parametrize("src, dst", [
    ("和", "Hoà"),  # vị trí dấu thanh
    ("林凡", unicodedata.normalize("NFD", "Lâm Phàm")),  # dán từ macOS
    ("林凡", "LÂM   PHÀM "),
    ("林凡", "Lâm-Phàm"),
    ("比", "Tỉ"),
    ("贵", "Quí"),
])
def test_review_focus_2_spelling_forms(src, dst):
    assert check_predictable(src, dst, "character", None, R)[0] is True


@pytest.mark.parametrize("src, dst", [("伊万·彼得", "Y Vạn Bỉ Đắc"), ("三2", "Tam Hai"), ("林凡", "Lâm Phàm Nhi")])
def test_review_focus_2_non_han_or_wrong_length_is_false(src, dst):
    assert check_predictable(src, dst, "character", None, R) == (False, frozenset())


def test_syllable_key():
    assert syllable_key("hòa") == syllable_key("hoà") == syllable_key("HOÀ") == "hoa2"
    assert syllable_key("tỷ") == syllable_key("tỉ") == "ti3"
    assert syllable_key("quý") == syllable_key("quí") == "qui1"
    assert syllable_key("huy") == "huy" and syllable_key("tô") != syllable_key("to")
    assert syllables("  Lâm-Phàm  nhi ") == ["Lâm", "Phàm", "nhi"]


def test_normalize_reading():
    assert normalize_reading(" Triệu ") == "triệu"
    assert normalize_reading(unicodedata.normalize("NFD", "TÀNG")) == "tàng"
    assert normalize_reading("triệu khải") is None
    assert normalize_reading("to1") is None
    assert normalize_reading("") is None and normalize_reading(None) is None and normalize_reading(3) is None


def test_reading_variants_title_case_and_capped():
    assert reading_variants("赵楷", R) == ["Triệu Khải", "Triệu Giai"]
    assert reading_variants("赵x", R) == [] and reading_variants("赵楷", {"赵": ["triệu"]}) == []
    many = {c: ["a", "b"] for c in "一二三四五六七"}
    assert len(reading_variants("一二三四五六七", many)) == 64


def test_learn_pairs():
    assert learn_pairs("赵楷", "Triệu Khải", "character", None) == [("赵", "triệu"), ("楷", "khải")]
    assert learn_pairs("赵楷", "Triệu Khải", "character", "ja") == []
    assert learn_pairs("赵楷", "Triệu", "character", None) == []
    assert learn_pairs("宗门", "Tông môn", "term", None) == []
    assert han_chars("赵楷") == ["赵", "楷"] and han_chars("赵·楷") is None
