import pytest

from app.core.lines import has_chinese, is_meta_line, split_long_line


@pytest.mark.parametrize(
    "line,meta",
    [
        ("第16章 你过河 我拆桥（求收藏，求推荐，求打赏）", False),
        ("=========================", True),
        ("Nguồn: https://www.69shuba.com/txt/35466/24881048", True),
        ("https://example.com/中文", True),
        ("", True),
        ("   ", True),
        ("……", True),
        ("＝＝＝＝", True),
        ("***", True),
        ("　　在赵楷准备离开东京汴梁的时候。", False),
    ],
)
def test_is_meta_line(line, meta):
    assert is_meta_line(line) is meta


def test_has_chinese_extension_a():
    assert has_chinese("㐀") is True
    assert has_chinese("abc，。") is False


def test_short_line_untouched():
    assert split_long_line("短句。") == ["短句。"]


def test_split_on_sentence_punctuation_keeps_text_and_quotes():
    sentence = "他说：“你终于醒了。”她点头。"
    text = sentence * 30
    parts = split_long_line(text, max_chars=50)
    assert "".join(parts) == text
    assert all(len(p) <= 50 for p in parts)
    assert not any(p.startswith("”") for p in parts)


def test_long_line_without_sentence_punctuation_falls_back_to_commas():
    text = "，".join(["赵楷准备离开东京汴梁"] * 40)
    parts = split_long_line(text, max_chars=250)
    assert "".join(parts) == text
    assert len(parts) > 1 and all(len(p) <= 250 for p in parts)


def test_long_line_without_any_punctuation_is_hard_cut():
    text = "赵" * 600
    parts = split_long_line(text, max_chars=250)
    assert parts == ["赵" * 250, "赵" * 250, "赵" * 100]


def test_count_han():
    from app.core.lines import count_han

    assert count_han("第1章 大王（abc）疯了") == 6
    assert count_han("=== Nguồn: http://x") == 0
