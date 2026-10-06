from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.core.translator import FakeTranslator
from app.services.import_analysis import StoredFile, analyze, default_title, suggest_title

BODY = "他走了很远的路，终于到了。" * 20


@pytest.fixture
def make_file(tmp_path):
    def make(name: str, data: bytes) -> StoredFile:
        p = tmp_path / f"f{len(list(tmp_path.iterdir()))}"
        p.write_bytes(data)
        return StoredFile(name, str(p), len(data))

    return make


def chapter_bytes(title: str) -> bytes:
    return f"{title}\n=========\nNguồn: https://x\n\n{BODY}\n".encode()


@dataclass
class SpyTranslator(FakeTranslator):
    kwargs: list = field(default_factory=list)

    def translate(self, texts, *, beam, batch_size):
        self.kwargs.append((beam, batch_size))
        return super().translate(texts, beam=beam, batch_size=batch_size)


class BrokenTranslator:
    model_id = "broken"

    def translate(self, texts, *, beam, batch_size):
        raise RuntimeError("model chưa tải")


def test_multi_orders_files_and_translates_titles_once(make_file):
    files = [
        make_file("0010 - c.txt", chapter_bytes("第10章 标题十（求收藏）")),
        make_file("0001 - a.txt", chapter_bytes("第1章 标题一（求收藏，求推荐）")),
        make_file("0002 - b.txt", chapter_bytes("第2章 标题二")),
    ]
    spy = SpyTranslator()
    a = analyze(files, mode="multi", translator=spy, source_name="大宋有种--35466")
    assert [c.key for c in a.chapters] == ["c0001", "c0002", "c0003"]
    assert [c.title_number for c in a.chapters] == [1, 2, 10]
    assert [c.title_machine for c in a.chapters] == ["VI<标题一>", "VI<标题二>", "VI<标题十>"]
    assert spy.calls == [["标题一", "标题二", "标题十"]]  # AC-2.3: không có chuỗi quảng cáo
    assert spy.kwargs == [(1, 64)]  # BR-2.5
    assert a.suggested_title_zh == "大宋有种"
    assert a.total_chars == sum(c.chars for c in a.chapters)
    assert default_title(a.chapters[2].to_json()) == "Chương 10: VI<标题十>"


def test_duplicate_titles_translated_once(make_file):
    files = [make_file("1.txt", chapter_bytes("番外")), make_file("2.txt", chapter_bytes("番外"))]
    fake = FakeTranslator()
    analyze(files, mode="multi", translator=fake)
    assert fake.calls == [["番外"]]


def test_single_mode_splits_and_marks_prologue(make_file):
    text = "大宋有种\n\n" + "\n".join(f"第{i}章 标题{i}\n{BODY}" for i in (1, 2, 3))
    a = analyze([make_file("大宋有种.txt", text.encode())], mode="single", translator=FakeTranslator())
    assert len(a.chapters) == 4
    first = a.chapters[0]
    assert first.is_prologue and first.selected and first.title_machine is None
    assert default_title(first.to_json()) == "Mở đầu"
    assert a.suggested_title_zh == "大宋有种"


def test_gbk_auto_and_forced_utf8(make_file):
    # AC-2.6
    f = make_file("a.txt", ("第1章 开始\n" + BODY).encode("gbk"))
    assert analyze([f], mode="multi").chapters[0].title_zh == "第1章 开始"
    forced = analyze([f], mode="multi", encoding="utf-8")
    assert forced.chapters == []
    assert forced.file_errors[0]["file"] == "a.txt"
    assert forced.file_errors[0]["code"] == "ENCODING"


def test_unsupported_and_too_large_files_reported(make_file):
    files = [
        make_file("catalog.json", b"{}"),
        StoredFile("big.txt", None, 60_000_000),
        make_file("0001.txt", chapter_bytes("第1章 甲")),
    ]
    a = analyze(files, mode="multi")
    assert {e["code"] for e in a.file_errors} == {"UNSUPPORTED_TYPE", "FILE_TOO_LARGE"}
    assert len(a.chapters) == 1


@pytest.mark.parametrize("translator", [None, BrokenTranslator()])
def test_missing_or_broken_model_keeps_preview(make_file, translator):
    # Review Focus 5
    a = analyze([make_file("0001.txt", chapter_bytes("第1章 甲"))], mode="multi", translator=translator)
    ch = a.chapters[0]
    assert ch.title_machine is None
    assert "TITLE_UNTRANSLATED" in ch.warnings
    assert ch.selected is True  # cảnh báo dịch tiêu đề không làm bỏ chọn chương
    assert default_title(ch.to_json()) == "Chương 1"


def test_short_and_author_note_deselected(make_file):
    files = [
        make_file("0001.txt", chapter_bytes("第1章 甲")),
        make_file("0002.txt", "第2章 上架感言\n谢谢".encode()),
    ]
    a = analyze(files, mode="multi", translator=FakeTranslator())
    assert [c.selected for c in a.chapters] == [True, False]
    assert set(a.chapters[1].warnings) == {"SHORT", "AUTHOR_NOTE"}


def test_default_title_uses_final_number_when_title_has_none():
    ch = {"is_prologue": False, "title_number": None, "no": 5, "title_machine": "X"}
    assert default_title(ch) == "Chương 5: X"
    assert default_title(ch, 3) == "Chương 3: X"


@pytest.mark.parametrize(
    "names,source_name,mode,expected",
    [
        (["大宋有种--35466/0001.txt", "大宋有种--35466/0002.txt"], None, "multi", "大宋有种"),
        (["0001 - 第1章.txt"], None, "multi", None),
        (["大宋有种--35466.txt"], None, "single", "大宋有种"),
        (["a.txt"], "我的书-12", "multi", "我的书"),
    ],
)
def test_suggest_title(names, source_name, mode, expected):
    assert suggest_title(names, source_name, mode) == expected


def test_to_json_has_no_text(make_file):
    a = analyze([make_file("0001.txt", chapter_bytes("第1章 甲"))], mode="multi")
    assert "text" not in a.chapters[0].to_json()
    assert a.chapters[0].text.startswith("第1章 甲\n=========")
