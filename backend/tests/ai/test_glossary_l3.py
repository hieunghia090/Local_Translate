from app.core.glossary import GlossaryIndex, Term
from app.deepseek import guards
from app.deepseek.glossary_select import PromptTerm, select_terms


def _index(terms):
    return GlossaryIndex(Term(t.id, t.src, t.dst) for t in terms)


def test_ac_8_3_seven_thousand_terms_l3_leaves_seventeen_lines():
    terms = [PromptTerm(str(i), chr(0x4E00 + i) + chr(0x6000 + i), f"Từ {i}", predictable=i < 28) for i in range(7000)]
    in_chapter = terms[:45]  # 45 term trong chương, 28 term đầu tự đoán được
    text = "，".join(t.src for t in in_chapter) + "。"
    sel = select_terms(text, _index(terms), {t.id: t for t in terms}, 120)
    assert (sel.matched, sel.sent, sel.skipped_predictable, sel.truncated) == (45, 17, 28, 0)
    assert len(sel.lines) == 17 and all("=" in line and not line.startswith("#") for line in sel.lines)
    assert set(sel.skipped_ids) == {str(i) for i in range(28)}
    assert sel.stats()["skipped_predictable"] == 28


def test_always_send_overrides_l3_and_truncation_counts_only_sent():
    terms = [PromptTerm("a", "林凡", "Lâm Phàm", predictable=True, always_send=True),
             PromptTerm("b", "苏雪", "Tô Tuyết", predictable=True),
             PromptTerm("c", "高俅", "Cao Cầu"), PromptTerm("d", "外门", "Ngoại môn đệ tử")]
    sel = select_terms("林凡苏雪高俅外门外门", _index(terms), {t.id: t for t in terms}, 2)
    assert sel.lines == ("外门=Ngoại môn đệ tử", "林凡=Lâm Phàm") and sel.skipped_ids == ("b",)
    assert (sel.matched, sel.sent, sel.truncated, sel.skipped_predictable) == (4, 2, 1, 1)


def test_ac_8_15_same_send_set_identical_block_even_with_different_predictable_terms():
    terms = [PromptTerm("a", "高俅", "Cao Cầu"), PromptTerm("b", "外门", "Ngoại môn đệ tử"),
             PromptTerm("c", "林凡", "Lâm Phàm", predictable=True)]
    idx, info = _index(terms), {t.id: t for t in terms}
    a = select_terms("高俅走进外门。林凡。", idx, info)
    b = select_terms("外门里，高俅说话。", idx, info)
    assert "\n".join(a.lines).encode() == "\n".join(b.lines).encode()


def test_g4_hanviet_variant_autofix_with_word_boundary():
    idx = GlossaryIndex([Term("t", "外门", "Ngoại môn đệ tử"), Term("s", "苏", "Tô Đại")])
    r = guards.check_glossary("他进了外门", "Hắn vào ngoại môn.", idx, {"t": ("Ngoại Môn",)})
    assert (r.dst, r.autofixed, r.missed) == ("Hắn vào Ngoại môn đệ tử.", ("t",), ())
    r = guards.check_glossary("苏说", "Tôi nói.", idx, {"s": ("Tô",)})  # "Tô" không được khớp vào "Tôi"
    assert (r.dst, r.missed) == ("Tôi nói.", ("s",))
