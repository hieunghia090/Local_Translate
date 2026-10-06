import pytest

from app.core.splitter import DraftChapter, chapter_from_file, default_selection, order_files, split_single

BODY = "他走了很远的路，终于到了。" * 20  # 220 chữ Hán


def make_book(n: int) -> str:
    return "\n".join(f"第{i}章 标题{i}\n{BODY}\n" for i in range(1, n + 1))


def test_auto_splits_248_chapters_plus_author_note():
    # AC-2.2
    drafts = split_single(make_book(248) + "\n作者的话\n感谢大家的支持，明天继续更新。\n")
    assert len(drafts) == 249
    assert drafts[0].title_zh == "第1章 标题1"
    assert drafts[-1].title_zh == "作者的话"
    selections = [default_selection(d) for d in drafts]
    assert all(sel for sel, _ in selections[:-1])
    selected, warnings = selections[-1]
    assert selected is False and "AUTHOR_NOTE" in warnings


def test_prologue_with_chinese_becomes_first_selected_chapter():
    # BR-2.2
    drafts = split_single("大宋有种\n作者：某人\n\n" + make_book(2))
    assert len(drafts) == 3
    assert drafts[0].is_prologue and drafts[0].title_zh == "大宋有种"
    assert default_selection(drafts[0]) == (True, ["SHORT"])


def test_preamble_without_chinese_dropped():
    assert len(split_single("Title\n===\n\n" + make_book(2))) == 2


def test_no_heading_gives_one_chapter():
    drafts = split_single("前言\n" + BODY)
    assert [d.title_zh for d in drafts] == ["前言"]


def test_meta_lines_stay_in_chapter_text():
    drafts = split_single("第1章 甲\n=====\nNguồn: http://x\n\n" + BODY)
    assert drafts[0].text.startswith("第1章 甲\n=====\nNguồn: http://x\n")
    assert drafts[0].chars == 223  # 第, 章, 甲 + 220 chữ của BODY


def test_vietnamese_and_markdown_headings():
    drafts = split_single("Chương 1 Mở\n" + BODY + "\n## 第2章 乙\n" + BODY)
    assert [d.title_zh for d in drafts] == ["Chương 1 Mở", "## 第2章 乙"]


def test_repeated_title_inside_body_does_not_split():
    # Review Focus 2: ch.131 của 大宋有种 lặp lại tiêu đề trong thân chương
    text = "第131章 赵佶之死\n" + BODY + "\n第131章赵佶之死——庄宗崩（求收藏）\n" + BODY + "\n第132章 下一章\n" + BODY
    assert [d.title_zh for d in split_single(text)] == ["第131章 赵佶之死", "第132章 下一章"]


def test_prose_lines_like_headings_do_not_split():
    # Review Focus 2
    text = "第565章 番外\n" + BODY + "\n第二回是在开封府城外开战，这次何灌没有跑了\n第一回合他们打得难解难分。\n" + BODY
    assert len(split_single(text)) == 1


def test_volume_heading_resets_numbering():
    text = "第一卷 起\n第1章 a\n" + BODY + "\n第2章 b\n" + BODY + "\n第二卷 承\n第1章 c\n" + BODY
    assert [d.title_zh for d in split_single(text)] == ["第一卷 起", "第1章 a", "第2章 b", "第二卷 承", "第1章 c"]


def test_blank_lines_rule_needs_two_blank_lines():
    text = "甲章\n" + BODY + "\n\n\n乙章\n" + BODY + "\n\n丙段\n" + BODY
    assert [d.title_zh for d in split_single(text, "blank_lines")] == ["甲章", "乙章"]


def test_regex_rule():
    drafts = split_single("前言内容\n卷一 开始\n内容一\n卷二 结束\n内容二", "regex", r"^卷")
    assert [d.title_zh for d in drafts] == ["前言内容", "卷一 开始", "卷二 结束"]
    assert drafts[0].is_prologue


def test_unknown_rule_raises():
    with pytest.raises(ValueError):
        split_single("x", "magic")


def test_order_files_by_leading_number_then_name():
    # BR-2.4
    names = ["manifest.txt", "0010 - 第10章.txt", "0002 - 第2章.txt", "大宋/0001 - 第1章.txt", "b.txt", "a.txt"]
    assert [names[i] for i in order_files(names)] == [
        "大宋/0001 - 第1章.txt", "0002 - 第2章.txt", "0010 - 第10章.txt", "a.txt", "b.txt", "manifest.txt",
    ]


def test_chapter_from_file_title_is_first_non_empty_line():
    d = chapter_from_file("0016.txt", "\n\n第16章 你过河\n===\nNguồn: x\n\n" + BODY)
    assert d.title_zh == "第16章 你过河"
    assert d.text.startswith("第16章 你过河\n===")
    assert d.chars == 225  # 第, 章, 你, 过, 河 + 220


def test_chapter_from_file_without_text_uses_file_stem():
    d = chapter_from_file("x/abc.txt", "\n\n")
    assert (d.title_zh, d.chars) == ("abc", 0)


def test_default_selection_rules():
    # BR-2.3
    assert default_selection(DraftChapter("第1章 短", "短" * 10, 10)) == (False, ["SHORT"])
    assert default_selection(DraftChapter("第2章 长", "长" * 300, 300)) == (True, [])
    assert default_selection(DraftChapter("第150章 上架感言", "字" * 300, 300)) == (False, ["AUTHOR_NOTE"])
    assert default_selection(DraftChapter("第146章 走（后天就要上架啦！）", "字" * 300, 300)) == (True, [])
