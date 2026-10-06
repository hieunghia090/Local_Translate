from app.core.glossary import GlossaryIndex, Term
from app.core.placeholders import PSEUDO_NAMES, mask, repair_with_aliases, unmask

ZHAO = Term("t1", "赵楷", "Triệu Khải", ("Triệu Giai", "Triệu Giai Nhi"))
ZONG = Term("t2", "宗门", "tông môn")
ZONG_BIG = Term("t3", "宗门大比", "Tông môn đại tỷ")
ZHAO1 = Term("t4", "赵", "Triệu")


def test_longest_match_first_and_no_overlap():
    # BR-4.1, Review Focus 1
    idx = GlossaryIndex([ZONG, ZONG_BIG, ZHAO, ZHAO1])
    found = [(m.start, m.end, m.term.id) for m in idx.find("宗门大比上赵楷与赵佶在宗门")]
    assert found == [(0, 4, "t3"), (5, 7, "t1"), (8, 9, "t4"), (11, 13, "t2")]


def test_count_by_term():
    idx = GlossaryIndex([ZONG, ZONG_BIG, ZHAO])
    assert idx.count("宗门大比，宗门，宗门，赵楷") == {"t3": 1, "t2": 2, "t1": 1}


def test_empty_index_is_falsy():
    assert not GlossaryIndex([])
    assert GlossaryIndex([]).find("赵楷") == []


def test_mask_assigns_pseudo_names_in_order():
    idx = GlossaryIndex([ZHAO, ZONG_BIG])
    m = mask("赵楷参加宗门大比，赵楷赢了", idx)
    a, b, c = PSEUDO_NAMES[:3]
    assert m.text == f"{a}参加{b}，{c}赢了"
    assert [s[0] for s in m.slots] == [a, b, c]
    assert m.hits == ("t1", "t3")


def test_mask_skips_names_already_in_text():
    idx = GlossaryIndex([ZHAO])
    m = mask(f"{PSEUDO_NAMES[0]}说赵楷", idx)
    assert m.slots[0][0] == PSEUDO_NAMES[1]


def test_mask_without_matches_returns_text_unchanged():
    m = mask("今天天气很好", GlossaryIndex([ZHAO]))
    assert (m.text, m.slots, m.hits) == ("今天天气很好", (), ())


def test_more_matches_than_names_leaves_rest_for_model():
    idx = GlossaryIndex([ZHAO])
    text = "赵楷" * (len(PSEUDO_NAMES) + 2)
    m = mask(text, idx)
    assert len(m.slots) == len(PSEUDO_NAMES)
    assert m.text.endswith("赵楷赵楷")


def test_unmask_restores_dst():
    idx = GlossaryIndex([ZHAO, ZONG_BIG])
    m = mask("赵楷参加宗门大比", idx)
    a, b = PSEUDO_NAMES[:2]
    assert unmask(f"{a} tham gia {b}.", m.slots) == "Triệu Khải tham gia Tông môn đại tỷ."


def test_unmask_fails_when_placeholder_lost_or_duplicated():
    # BR-4.3
    m = mask("赵楷参加宗门大比", GlossaryIndex([ZHAO, ZONG_BIG]))
    a, b = PSEUDO_NAMES[:2]
    assert unmask(f"{a} tham gia.", m.slots) is None
    assert unmask(f"{a} {a} tham gia {b}.", m.slots) is None


def test_repair_with_aliases_longest_first():
    # Review Focus 3
    out, ok = repair_with_aliases("Triệu Giai Nhi đi rồi", [ZHAO])
    assert (out, ok) == ("Triệu Khải đi rồi", True)


def test_repair_ok_when_dst_already_present_and_fails_otherwise():
    assert repair_with_aliases("Triệu Khải đi", [ZHAO]) == ("Triệu Khải đi", True)
    assert repair_with_aliases("Hắn đi", [ZHAO]) == ("Hắn đi", False)


def test_pseudo_names_are_safe():
    assert len(PSEUDO_NAMES) == len(set(PSEUDO_NAMES)) == 20
    assert all(n.isalpha() and n.isascii() and n[0].isupper() for n in PSEUDO_NAMES)


def test_repair_alias_respects_word_boundaries():
    an = Term("t5", "安儿", "An Nhi", ("An",))
    assert repair_with_aliases("Anh ấy gặp Tiểu An", [an]) == ("Anh ấy gặp Tiểu An Nhi", True)


def test_repair_fixes_two_aliases_in_one_output():
    out, ok = repair_with_aliases("Triệu Giai gặp Triệu Giai Nhi", [ZHAO])
    assert (out, ok) == ("Triệu Khải gặp Triệu Khải", True)
    a = Term("a", "甲", "Giáp", ("Giap",))
    b = Term("b", "乙", "Ất", ("At",))
    assert repair_with_aliases("Giap và At", [a, b]) == ("Giáp và Ất", True)


def test_repair_does_not_rereplace_existing_dst():
    t = Term("t6", "安", "An Nhi", ("An",))
    assert repair_with_aliases("An Nhi gặp An", [t]) == ("An Nhi gặp An Nhi", True)



def test_unmask_not_chained():
    a, b = PSEUDO_NAMES[:2]
    slots = ((a, Term("t1", "赵楷", f"Gặp {b}")), (b, Term("t2", "宗门", "tông môn")))
    assert unmask(f"{a} {b}", slots) == f"Gặp {b} tông môn"
