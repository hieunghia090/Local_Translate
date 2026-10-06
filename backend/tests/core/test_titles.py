import pytest

from app.core.titles import (
    default_title_vi,
    is_author_note,
    parse_cn_number,
    split_heading,
    strip_ads,
    translation_source,
)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("第16章 你过河 我拆桥（求收藏，求推荐，求打赏）", "第16章 你过河 我拆桥"),
        ("第256章 军事革命来了！金贼能等等吗（求订阅！求月票！）", "第256章 军事革命来了！金贼能等等吗"),
        ("第146章 去开封府怎么走？（后天就要上架啦！）", "第146章 去开封府怎么走？"),
        ("第9章 两更（求收藏）(求推荐)", "第9章 两更"),
        ("第5章 他（她）来了", "第5章 他（她）来了"),
        ("第7章 结局（下）", "第7章 结局（下）"),
        ("第68章 卖国求饶的事儿成了？（求收藏，求推荐）", "第68章 卖国求饶的事儿成了？"),
        ("第302章 赵构求见", "第302章 赵构求见"),
    ],
)
def test_strip_ads(title, expected):
    assert strip_ads(title) == expected


@pytest.mark.parametrize(
    "s,n",
    [("16", 16), ("１６", 16), ("十", 10), ("十二", 12), ("二十", 20), ("一百零五", 105),
     ("两千零一", 2001), ("一二三", 123), ("abc", None)],
)
def test_parse_cn_number(s, n):
    assert parse_cn_number(s) == n


@pytest.mark.parametrize(
    "title,expected",
    [
        ("第16章 你过河 我拆桥", (16, "你过河 我拆桥")),
        ("第一百零五回：大战", (105, "大战")),
        ("第134章不去五国城了！", (134, "不去五国城了！")),
        ("Chương 3: Abc", (3, "Abc")),
        ("# 第3章 标题", (3, "标题")),
        ("第一卷 风起", (None, "第一卷 风起")),
        ("楔子", (None, "楔子")),
    ],
)
def test_split_heading(title, expected):
    assert split_heading(title) == expected


def test_translation_source_drops_number_and_ads():
    # AC-2.3: phần quảng cáo trong ngoặc không được đưa vào model
    assert translation_source("第16章 你过河 我拆桥（求收藏，求推荐，求打赏）") == "你过河 我拆桥"


def test_default_title_vi():
    assert default_title_vi(16, 1, "Qua sông") == "Chương 16: Qua sông"
    assert default_title_vi(None, 7, "X") == "Chương 7: X"
    assert default_title_vi(3, 3, None) == "Chương 3"


@pytest.mark.parametrize(
    "title", ["第150章 上架感言", "第119章 大罗罗的三江感言", "作者的话", "请假条"]
)
def test_author_notes_detected(title):
    assert is_author_note(title)


@pytest.mark.parametrize(
    "title",
    [
        "第146章 去开封府怎么走？（后天就要上架啦！）",  # Review Focus 1: 上架 chỉ nằm trong quảng cáo
        "第147章 出兵，目标开封府！（明天就要上架啦！）",
        "第16章 你过河 我拆桥",
    ],
)
def test_real_chapters_not_author_notes(title):
    assert not is_author_note(title)
