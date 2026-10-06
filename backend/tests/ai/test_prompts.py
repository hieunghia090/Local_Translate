from app.core.glossary import GlossaryIndex, Term
from app.deepseek import prompts
from app.deepseek.estimate import (
    Coefficients, Sample, calibrate, cost_usd, error_pct, output_tokens_est, prompt_tokens_est, text_tokens,
)
from app.deepseek.glossary_select import PromptTerm, clean_note, select_terms

ANCIENT = {"kinship": True, "pronoun": True, "modern_stable": False}


def _index(terms: list[PromptTerm]) -> GlossaryIndex:
    return GlossaryIndex(Term(t.id, t.src, t.dst) for t in terms)


# ---------- ước tính ----------

def test_text_tokens_default_coefficients():
    assert text_tokens("字" * 18) == 10 and text_tokens("abcd") == 1 and text_tokens("") == 0
    assert text_tokens("字" * 18, 0.9) == 20


def test_prompt_and_output_estimates():
    c = Coefficients()
    assert prompt_tokens_est("字" * 18, "abcd", c) == 11
    assert output_tokens_est("字" * 18, c) == 33  # BR-8.24: × 3,3 (đo thực tế)
    assert output_tokens_est("字" * 18, c, share=0.10) == 4  # soát ≈ 10%


def test_calibrate_needs_ten_samples():
    s = [Sample(tokens_in=300, tokens_out=200, text_tokens=100, overhead_tokens=100)] * 9
    assert calibrate(s) == Coefficients(samples=9) and not calibrate(s).calibrated


def test_calibrate_moving_average_of_last_fifty():
    # BR-8.24b: (300 - 100) / 100 = 2 → 1,8 / 2 = 0,9 chữ / token; 300 / 200 = 1,5 ra / vào
    c = calibrate([Sample(tokens_in=300, tokens_out=300, text_tokens=100, overhead_tokens=100)] * 60)
    assert c.samples == 50 and c.calibrated and c.han_per_token == 0.9 and c.out_ratio == 1.5
    assert c.view() == {"han_per_token": 0.9, "out_ratio": 1.5, "samples": 50, "calibrated": True}


def test_cost_and_error_pct():
    assert cost_usd(tokens_in=1_000_000, tokens_in_cached=400_000, tokens_out=500_000,
                    price_in=0.27, price_cached=0.07, price_out=1.10) == 0.74
    assert error_pct([500, 400], [400, 300]) == 22.5
    assert error_pct([], []) is None


# ---------- prompt ----------

def test_system_prompt_is_stable_and_has_honorific_block():
    # BR-8.1
    a = prompts.system_prompt(None, "xianxia", ANCIENT)
    assert a == prompts.system_prompt(None, "xianxia", dict(ANCIENT))
    assert a.startswith(prompts.DEFAULT_FOUNDATION.strip())
    assert "# XƯNG HÔ" in a and "ta / ngươi" in a and "sư tỷ" in a


def test_modern_genre_and_custom_foundation():
    s = prompts.system_prompt("  Giọng văn hiện đại.  ", "urban", {"kinship": False, "pronoun": False, "modern_stable": True})
    assert s.startswith("Giọng văn hiện đại.\n\n# XƯNG HÔ") and "tôi / anh / em" in s and "sư tỷ" not in s


def test_user_prompt_sections_in_order():
    u = prompts.user_prompt(glossary_lines=["高俅=Cao Cầu"], context="Đoạn cuối.", notes=["Tên 高俅 phải là Cao Cầu"],
                            lines=[(1, "第16章 你过河"), (4, "在赵楷准备离开")])
    assert u.index("# THUẬT NGỮ") < u.index("# NGỮ CẢNH") < u.index("# GHI CHÚ SỬA") < u.index("# VĂN BẢN CẦN DỊCH")
    assert "高俅=Cao Cầu" in u and "- [correction] Tên 高俅 phải là Cao Cầu" in u
    assert u.endswith("# VĂN BẢN CẦN DỊCH\n⟦1⟧ 第16章 你过河\n⟦4⟧ 在赵楷准备离开")


def test_user_prompt_omits_empty_sections():
    assert prompts.user_prompt(glossary_lines=[], context=None, notes=[], lines=[(0, "你好")]) == "# VĂN BẢN CẦN DỊCH\n⟦0⟧ 你好"


def test_tail_context_keeps_last_600_chars():
    # BR-8.4
    lines = [f"Câu {i} " + "x" * 90 for i in range(20)]
    ctx = prompts.tail_context(lines)
    assert len(ctx) <= 600 and ctx.endswith(lines[-1]) and lines[0] not in ctx
    assert prompts.tail_context(["", "  "]) is None
    assert len(prompts.tail_context(["y" * 1000])) == 600


def test_review_prompt_marks_locked_lines():
    u = prompts.review_user_prompt(["高俅=Cao Cầu"], [prompts.ReviewItem(4, "高俅来了", "Cao Cừu tới.", False),
                                                     prompts.ReviewItem(5, "他走了", "Hắn đi.", True)])
    assert "# THUẬT NGỮ\n高俅=Cao Cầu" in u
    assert "⟦4⟧ 高俅来了\n⟹ Cao Cừu tới." in u and "⟦5⟧ [KHÔNG SỬA] 他走了\n⟹ Hắn đi." in u
    assert "json" in prompts.REVIEW_SYSTEM.lower()  # json_object mode cần chữ "json" trong prompt


def test_split_lines_by_token_budget():
    # BR-8.7: mỗi dòng ~52 token
    lines = [(i, "字" * 90) for i in range(10)]
    assert [len(p) for p in prompts.split_lines(lines, 120)] == [2, 2, 2, 2, 2]
    assert prompts.split_lines(lines, 10_000) == [lines]
    assert prompts.split_lines([], 100) == []


# ---------- glossary ----------

def test_l1_l2_only_terms_in_chapter_sorted_by_src():
    terms = [PromptTerm("1", "苏清雪", "Tô Thanh Tuyết", "nữ, sư tỷ của Lâm Phàm (ch. 12)"),
             PromptTerm("2", "外门", "Ngoại môn đệ tử"), PromptTerm("3", "不在", "Không có")]
    sel = select_terms("苏清雪走进外门。外门很大。", _index(terms), {t.id: t for t in terms}, 120)
    assert sel.lines == ("外门=Ngoại môn đệ tử", "苏清雪=Tô Thanh Tuyết (nữ, sư tỷ của Lâm Phàm)")
    assert (sel.matched, sel.sent, sel.truncated, sel.skipped_predictable) == (2, 2, 0, 0) and sel.tokens_est > 0
    assert sel.stats() == {"matched": 2, "sent": 2, "skipped_predictable": 0, "truncated": 0, "tokens_est": sel.tokens_est}


def test_clean_note_strips_volatile_and_caps_length():
    note = clean_note("nữ, sư tỷ (ch. 12) của Lâm Phàm, xuất hiện từ chương 3 với vai trò rất quan trọng trong tông môn")
    assert "ch. 12" not in note and len(note) <= 60 and "  " not in note
    assert clean_note(None) == "" and clean_note("（第3章）") == ""


def test_l4_keeps_most_frequent_terms():
    # AC-8.14: 200 term khớp, giới hạn 120
    terms = [PromptTerm(str(i), "词" + chr(0x4E00 + i), f"Từ {i}") for i in range(200)]
    text = "".join(t.src * (2 if i < 120 else 1) for i, t in enumerate(terms))
    sel = select_terms(text, _index(terms), {t.id: t for t in terms}, 120)
    assert len(sel.lines) == 120 and sel.truncated == 80 and sel.matched == 200
    assert {line.split("=")[1] for line in sel.lines} == {f"Từ {i}" for i in range(120)}


def test_same_terms_give_identical_block():
    # AC-8.15
    terms = [PromptTerm("a", "林凡", "Lâm Phàm"), PromptTerm("b", "苏清雪", "Tô Thanh Tuyết")]
    idx, info = _index(terms), {t.id: t for t in terms}
    a = select_terms("林凡见苏清雪。", idx, info)
    b = select_terms("苏清雪说：林凡，林凡！", idx, info)
    assert "\n".join(a.lines).encode() == "\n".join(b.lines).encode()


def test_empty_index_selects_nothing():
    sel = select_terms("林凡", GlossaryIndex([]), {})
    assert sel.lines == () and sel.tokens_est == 0
