from app.core.glossary import GlossaryIndex, Term
from app.services.chapters import _spans


def test_dst_spans_do_not_overlap():
    idx = GlossaryIndex([Term("a", "赵楷", "Triệu Giai"), Term("b", "赵", "Triệu")])
    spans = _spans(idx, "赵楷和赵", "Triệu Giai và Triệu")
    assert [s["dst"] for s in spans] == [[0, 10], [14, 19]]
