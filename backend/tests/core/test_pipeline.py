from dataclasses import dataclass

import pytest

from app.core.pipeline import TranslationCountMismatch, render, translate_segments
from app.core.segments import plan_segments
from app.core.translator import BatchResult, FakeTranslator

CHAPTER = (
    "第16章 你过河 我拆桥\n"
    "=========================\n"
    "Nguồn: https://www.69shuba.com/txt/35466/24881048\n"
    "\n"
    "　　在赵楷准备离开东京汴梁的时候。\n"
)


def test_translates_text_lines_and_keeps_meta_bytes():
    res = translate_segments(plan_segments(CHAPTER), FakeTranslator(), beam=2, batch_size=8)
    assert render(res) == (
        "VI<第16章 你过河 我拆桥>\n"
        "=========================\n"
        "Nguồn: https://www.69shuba.com/txt/35466/24881048\n"
        "\n"
        "　　VI<在赵楷准备离开东京汴梁的时候。>\n"
    )


def test_single_translator_call_with_all_parts_in_order():
    fake = FakeTranslator()
    translate_segments(plan_segments(CHAPTER), fake)
    assert fake.calls == [["第16章 你过河 我拆桥", "在赵楷准备离开东京汴梁的时候。"]]


def test_long_line_parts_joined_with_space():
    text = "他说。" * 200
    res = translate_segments(plan_segments(text, max_chars=60), FakeTranslator())
    parts = plan_segments(text, max_chars=60)[0].parts
    assert res.segments[0].dst == " ".join(f"VI<{p}>" for p in parts)


def test_token_totals_and_model_id():
    res = translate_segments(plan_segments(CHAPTER), FakeTranslator())
    assert res.tokens_in == len("第16章 你过河 我拆桥") + len("在赵楷准备离开东京汴梁的时候。")
    assert res.tokens_out > 0 and res.model_id == "fake"


def test_meta_only_chapter_does_not_call_translator():
    fake = FakeTranslator()
    text = "=====\nNguồn: https://x.y\n\n"
    res = translate_segments(plan_segments(text), fake)
    assert fake.calls == [] and render(res) == text and res.tokens_in == 0


def test_empty_text():
    res = translate_segments(plan_segments(""), FakeTranslator())
    assert render(res) == ""


@dataclass
class ShortTranslator:
    model_id: str = "short"

    def translate(self, texts, *, beam, batch_size):
        return BatchResult(["x"] * (len(texts) - 1), 1, 1, [False] * (len(texts) - 1))


def test_count_mismatch_raises():
    with pytest.raises(TranslationCountMismatch) as e:
        translate_segments(plan_segments(CHAPTER), ShortTranslator())
    assert e.value.expected == 2 and e.value.got == 1


@dataclass
class TruncatingTranslator:
    model_id: str = "trunc"

    def translate(self, texts, *, beam, batch_size):
        return BatchResult([f"T{i}" for i, _ in enumerate(texts)], 1, 1, [i == 1 for i, _ in enumerate(texts)])


def test_truncated_flag_lands_on_owning_segment():
    res = translate_segments(plan_segments(CHAPTER), TruncatingTranslator())
    assert res.segments[0].flags == [] and res.segments[4].flags == ["truncated"]


@dataclass
class NoTruncFlagsTranslator:
    model_id: str = "noflags"

    def translate(self, texts, *, beam, batch_size):
        return BatchResult(["x"] * len(texts), 1, 1, [])


def test_truncated_length_mismatch_raises():
    with pytest.raises(TranslationCountMismatch) as e:
        translate_segments(plan_segments(CHAPTER), NoTruncFlagsTranslator())
    assert e.value.expected == 2 and e.value.got == 0


@dataclass
class EmptyTranslator:
    model_id: str = "empty"

    def translate(self, texts, *, beam, batch_size):
        return BatchResult(["  "] * len(texts), 1, 1, [False] * len(texts))


def test_empty_model_output_is_flagged():
    res = translate_segments(plan_segments(CHAPTER), EmptyTranslator())
    assert res.segments[0].flags == ["empty_output"] and res.segments[4].flags == ["empty_output"]


def test_parts_without_chinese_are_kept_verbatim_not_sent():
    text = "赵" * 249 + "”””””"
    fake = FakeTranslator()
    res = translate_segments(plan_segments(text, max_chars=250), fake)
    assert fake.calls == [["赵" * 249 + "”"]]
    assert res.segments[0].dst == "VI<" + "赵" * 249 + "”> ””””"
