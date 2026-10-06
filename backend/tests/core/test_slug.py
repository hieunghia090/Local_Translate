from app.core.slug import fold, slugify, unique_slug


def test_fold_removes_vietnamese_marks():
    assert fold("Đại TỐNG Hữu Chủng") == "dai tong huu chung"


def test_slugify_vietnamese():
    assert slugify("Đại Tống Hữu Chủng") == "dai-tong-huu-chung"


def test_slugify_punctuation_and_spaces():
    assert slugify("  Ngươi qua sông, ta phá cầu!! ") == "nguoi-qua-song-ta-pha-cau"


def test_slugify_chinese_only_is_empty():
    assert slugify("大宋有种") == ""


def test_slugify_max_len_has_no_trailing_dash():
    assert slugify("a " * 50, max_len=10) == "a-a-a-a-a"


def test_unique_slug():
    assert unique_slug("x", set()) == "x"
    assert unique_slug("x", {"x", "x-2"}) == "x-3"
