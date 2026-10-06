import json

from app.services.glossary import parse_import

HEAD = "src_zh\tdst_vi\tcategory\taliases\tenabled\n"


def test_tsv_quote_in_dst_imports_verbatim():
    items = parse_import((HEAD + '赵楷\t"Triệu" Khải\tcharacter\t\t1\n').encode(), "g.tsv")
    assert items[0].dst_vi == '"Triệu" Khải'


def test_tsv_unbalanced_quote_does_not_swallow_rows():
    tsv = HEAD + '赵楷\t"Triệu\tcharacter\t\t1\n东京\tĐông Kinh\tlocation\t\t1\n汴梁\tBiện Lương\tlocation\t\t1\n'
    items = parse_import(tsv.encode(), "g.tsv")
    assert [i.src_zh for i in items] == ["赵楷", "东京", "汴梁"]


def test_json_aliases_as_string_split_on_pipe():
    data = json.dumps([{"src_zh": "赵楷", "dst_vi": "Triệu Khải", "aliases": "Triệu Giai|Giai"}])
    assert parse_import(data.encode(), "g.json")[0].aliases == ["Triệu Giai", "Giai"]


def test_json_aliases_of_other_type_rejected():
    import pytest

    from app.errors import AppError

    data = json.dumps([{"src_zh": "赵楷", "dst_vi": "Triệu Khải", "aliases": 5}])
    with pytest.raises(AppError):
        parse_import(data.encode(), "g.json")


def test_tsv_empty_or_missing_enabled_means_true():
    items = parse_import((HEAD + "赵楷\tA\tcharacter\t\t\n东京\tB\tlocation\t\t0\n").encode(), "g.tsv")
    assert [i.enabled for i in items] == [True, False]
    items = parse_import("src_zh\tdst_vi\n赵楷\tA\n".encode(), "g.tsv")
    assert items[0].enabled is True
    items = parse_import((HEAD + "赵楷\tA\n").encode(), "g.tsv")  # hàng ngắn, thiếu cột enabled
    assert items[0].enabled is True
