import pytest

from app.core.glossary import GlossaryIndex, Term
from app.deepseek import guards
from app.deepseek.client import OutputRejected


def test_g1_rejects_chinese_output():
    with pytest.raises(OutputRejected, match="Bản dịch vẫn còn là tiếng Trung"):
        guards.check_not_chinese("⟦1⟧ 这是中文这是中文这是中文这是中文这是中文")
    guards.check_not_chinese("⟦1⟧ 你好")  # dưới 20 chữ cái: không xét
    guards.check_not_chinese("⟦1⟧ Lâm Phàm mở mắt nhìn 林凡 rồi đi tiếp vào sân")


def test_g6_strips_preamble_only_on_first_line():
    text, removed = guards.strip_preamble("\nĐây là bản dịch của bạn:\n⟦1⟧ Một\n⟦2⟧ Hai")
    assert removed and text == "\n⟦1⟧ Một\n⟦2⟧ Hai"
    assert guards.strip_preamble("⟦1⟧ Được rồi, đi thôi.") == ("⟦1⟧ Được rồi, đi thôi.", False)
    assert guards.strip_preamble("Một câu bình thường\n⟦1⟧ x") == ("Một câu bình thường\n⟦1⟧ x", False)


def test_parse_marked_handles_noise_continuations_and_duplicates():
    # Review Focus 3
    got = guards.parse_marked("Được rồi:\n⟦3⟧ Một\ncâu tiếp\n\n⟦5⟧Hai\n⟦3⟧ lặp lại\n  ⟦7⟧  Ba  ")
    assert got == {3: "Một câu tiếp", 5: "Hai", 7: "Ba"}


def test_g2_small_gap_returns_missing_large_gap_rejects():
    sent = list(range(100))
    got = {i: "x" for i in sent if i not in (10, 20)}
    assert guards.check_markers(sent, got) == [10, 20]
    assert guards.check_markers(sent, {**got, 30: "  "}) == [10, 20, 30]  # dòng rỗng tính là thiếu
    with pytest.raises(OutputRejected) as e:
        guards.check_markers(sent, {i: "x" for i in range(95)})
    assert e.value.code == "markers"


def test_g3_residual_han():
    assert guards.has_residual_han("Hắn nói 这是什么东西")
    assert not guards.has_residual_han("Lâm Phàm (林凡) đi vào sân rồi ngồi xuống")


def test_g4_alias_autofix_and_miss():
    # AC-8.6 và tự sửa bằng alias
    idx = GlossaryIndex([Term("t1", "林凡", "Lâm Phàm", ("Lâm Phạm",)), Term("t2", "高俅", "Cao Cầu")])
    r = guards.check_glossary("林凡对高俅说", "Lâm Phạm nói với Cao Cừu", idx)
    assert r.dst == "Lâm Phàm nói với Cao Cừu" and r.autofixed == ("t1",) and r.missed == ("t2",)
    ok = guards.check_glossary("林凡", "Lâm Phàm", idx)
    assert (ok.dst, ok.autofixed, ok.missed) == ("Lâm Phàm", (), ())
    assert guards.check_glossary("林凡", "x", GlossaryIndex([])).missed == ()


def test_g5_thresholds():
    assert guards.ratio_abnormal("translate", 100, 451) and not guards.ratio_abnormal("translate", 100, 450)
    assert not guards.ratio_abnormal("translate", 100, 330)  # đo thực tế: 3,3 lần token văn bản là bình thường
    assert guards.ratio_abnormal("review", 100, 151) and not guards.ratio_abnormal("review", 100, 150)
    assert guards.ratio_abnormal("extract", 100, 201)
    assert guards.ratio_abnormal("translate", 0, 5)


def test_clean_title():
    assert guards.clean_title("## Chương 16: Ngươi qua sông ") == "Chương 16: Ngươi qua sông"


def test_parse_review_json():
    assert guards.parse_review_json('```json\n{"fixes": [{"idx": 4}, "rác"]}\n```') == [{"idx": 4}]
    assert guards.parse_review_json('{"fixes": []}') == []
    with pytest.raises(OutputRejected):
        guards.parse_review_json("không phải json")
    with pytest.raises(OutputRejected):
        guards.parse_review_json('{"x": 1}')
