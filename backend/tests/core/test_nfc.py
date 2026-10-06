import unicodedata

from app.core.glossary import GlossaryIndex, Term
from app.deepseek import guards
from app.schemas import PreviewEdit, SuggestionAcceptItem, TermCreate, TermUpdate
from app.services.glossary import parse_import, to_term

NFC = unicodedata.normalize("NFC", "Lâm Phàm")
NFD = unicodedata.normalize("NFD", "Lâm Phàm")


def test_fixture_forms_differ():
    assert NFC != NFD


def test_schemas_normalize_to_nfc():
    c = TermCreate(src_zh="林凡", dst_vi=NFD, aliases=[NFD, NFC, unicodedata.normalize("NFD", "Lâm Phạm")])
    assert c.dst_vi == NFC and c.aliases == [NFC, "Lâm Phạm"]
    u = TermUpdate(dst_vi=NFD, aliases=[unicodedata.normalize("NFD", "Lâm Phạm")])
    assert u.dst_vi == NFC and u.aliases == ["Lâm Phạm"]
    assert PreviewEdit(dst_vi=NFD).dst_vi == NFC
    import uuid
    assert SuggestionAcceptItem(id=uuid.uuid4(), dst_vi=NFD).dst_vi == NFC


def test_src_normalized_nfc():
    # U+F900 (chữ tương thích) có phân rã chuẩn thành U+8C48
    assert TermCreate(src_zh="豈", dst_vi="x").src_zh == "豈"


def test_import_normalizes():
    tsv = "src_zh\tdst_vi\tcategory\taliases\tenabled\n林凡\t" + NFD + "\tcharacter\t\t1\n"
    assert parse_import(tsv.encode(), "g.tsv")[0].dst_vi == NFC


def test_g4_nfd_term_vs_nfc_output_is_not_autofixed():
    # Đúng kịch bản review: dst_vi trong DB là NFD, model trả NFC -> không được "sửa" sang NFD, không miss.
    index = GlossaryIndex([Term("1", "林凡", NFD, (unicodedata.normalize("NFD", "Lâm Phạm"),))])
    out = f"{NFC} đi rồi."
    check = guards.check_glossary("林凡走了", out, index)
    assert (check.dst, check.autofixed, check.missed) == (out, (), ())


def test_g4_autofix_result_is_nfc_when_alias_nfd_and_output_nfc():
    index = GlossaryIndex([Term("1", "林凡", NFD, (unicodedata.normalize("NFD", "Lâm Phạm"),))])
    check = guards.check_glossary("林凡走了", "Lâm Phạm đi rồi.", index)
    assert check.autofixed == ("1",) and check.dst == "Lâm Phàm đi rồi."
    assert unicodedata.is_normalized("NFC", check.dst)


def test_g4_variants_compared_in_nfc():
    index = GlossaryIndex([Term("1", "林凡", NFC)])
    check = guards.check_glossary("林凡走了", "lâm phạm đi.", index, {"1": [unicodedata.normalize("NFD", "lâm phạm")]})
    assert check.autofixed == ("1",) and check.dst == "Lâm Phàm đi."


def test_to_term_normalizes():
    class T:
        id, src_zh, dst_vi, aliases = "1", "林凡", NFD, [NFD]

    t = to_term(T())
    assert t.dst == NFC and t.aliases == (NFC,)
