import json

import pytest

from app.deepseek import extract as core
from app.deepseek.client import OutputRejected


def t(src, dst, type_="character", conf=95, lang="zh", notes=""):
    return {"type": type_, "source_term": src, "suggested_target": dst, "name_lang": lang, "confidence": conf, "notes": notes}


def test_fixed_system_and_user_prompt_without_glossary():
    # AC-4.13: system cố định; user chỉ có loại cần trích và văn bản
    assert "json" in core.EXTRACT_SYSTEM.lower() and "Không bao giờ để nguyên chữ Hán" in core.EXTRACT_SYSTEM
    assert core.extract_user_prompt(" 赵楷来了 ", ["character", "realm"]) == "# LOẠI CẦN TRÍCH\ncharacter, realm\n\n# VĂN BẢN\n赵楷来了"


def test_make_batches_packs_lines_up_to_limit():
    line = "字" * 99
    chapters = [core.ChapterText(i, i, f"第{i}章 标题\n=========\nNguồn: http://x\n" + "\n".join([line] * 50)) for i in (1, 2, 3)]
    batches = core.make_batches(chapters)
    assert all(len(b.text) <= core.BATCH_CHARS for b in batches)
    assert len(batches) == 2 and batches[0].chapter_ids == (1, 2, 3) and batches[1].chapter_ids == (3,)
    assert (batches[0].first_no, batches[0].last_no) == (1, 3)
    assert "Nguồn" not in batches[0].text and "=====" not in batches[0].text  # dòng meta không gửi


def test_make_batches_hard_splits_giant_line_and_skips_empty_chapter():
    giant = core.ChapterText("g", 1, "字" * 30_000)
    empty = core.ChapterText("e", 2, "=========\n\n")
    batches = core.make_batches([giant, empty])
    assert [len(b.text) for b in batches] == [12_000, 12_000, 6_000]
    assert all(b.chapter_ids == ("g",) for b in batches)


def test_parse_terms_shapes():
    assert core.parse_terms('{"terms": [{"type": "character"}]}') == ([{"type": "character"}], False)
    assert core.parse_terms('```json\n{"terms": []}\n```') == ([], False)
    assert core.parse_terms('[{"a": 1}]') == ([{"a": 1}], False)
    with pytest.raises(OutputRejected):
        core.parse_terms("không phải json")
    with pytest.raises(OutputRejected):
        core.parse_terms('{"x": 1}')


def test_ac_4_12_salvage_37_complete_objects():
    items = [t(f"名{i:02d}", f"Tên {i}", notes="có } và { trong chuỗi" if i == 5 else "") for i in range(40)]
    full = json.dumps({"terms": items}, ensure_ascii=False)
    cut = full.index(json.dumps(items[37], ensure_ascii=False)) + 20
    got, salvaged = core.parse_terms(full[:cut])
    assert salvaged is True and len(got) == 37 and got[5]["notes"] == "có } và { trong chuỗi"


def test_filters_in_order_ac_4_10_ac_4_11():
    text = "赵楷来了。赵楷走了。苏清雪笑。苏清雪哭。王五来。林凡。林凡。高俅。高俅。玉佩。玉佩。"
    raw = [
        t("赵楷", "Triệu Khải", conf=92),
        t("赵楷", "Triệu Giai"),  # trùng trong lô
        {"type": "person", "source_term": "苏清雪", "suggested_target": "Tô Thanh Tuyết", "confidence": 90},  # AC-4.11
        t("苏清雪", "苏清雪"),  # AC-4.10: còn chữ Hán
        t("王五", "Vương Ngũ"),  # chỉ 1 lần
        t("林凡", "Lâm Phàm"),  # đã có trong glossary
        t("高俅", "Cao Cầu"),  # đã bị từ chối
        t("玉佩", "Ngọc bội", type_="item"),  # loại không được chọn
        t("赵五", "Triệu Ngũ", lang="xx"),
        t("赵六", "Triệu Lục", conf="cao"),
        "rác",
    ]
    kept, drops = core.filter_proposals(raw, text, known={"林凡"}, rejected={"高俅"},
                                        categories=list(core.DEFAULT_CATEGORIES))
    assert [(p.src, p.dst, p.confidence) for p in kept] == [("赵楷", "Triệu Khải", 92)]
    assert kept[0].context.startswith("赵楷来了")
    assert drops.view() == {"dropped_invalid": 5, "dropped_existing": 3, "dropped_han_target": 1, "dropped_low_freq": 1}


def test_merge_across_batches():
    p = lambda dst, conf: core.Proposal("赵楷", dst, "character", "zh", conf, "", "")
    merged = core.merge_proposals([[p("Triệu Khải", 90)], [p("Triệu Giai", 70)], [p("Triệu Khải", 80)]])
    assert [(m.src, m.dst, m.confidence) for m in merged] == [("赵楷", "Triệu Khải", 80)]


def test_drops_add():
    a, b = core.Drops(1, 2, 3, 4), core.Drops(invalid=1)
    a.add(b)
    assert a.view()["dropped_invalid"] == 2
