from pathlib import Path

import pytest

from app.core.glossary import GlossaryIndex, Term
from app.core.pipeline import translate_segments
from app.core.segments import plan_segments
from app.core.textio import decode_source

SOURCE = Path(__file__).resolve().parents[3] / "大宋有种--35466"


@pytest.mark.slow
@pytest.mark.skipif(not SOURCE.is_dir(), reason="thiếu thư mục 大宋有种--35466")
def test_real_model_keeps_glossary_terms():
    # AC-4.1 với HachimiMT thật
    from app.core.ct2_translator import CT2Translator

    text = "\n".join(decode_source(p.read_bytes()).text for p in sorted(SOURCE.glob("000[1-5] *.txt")))
    index = GlossaryIndex([
        Term("a", "赵楷", "Triệu Khải"), Term("b", "赵佶", "Triệu Cát"), Term("c", "童贯", "Đồng Quán"),
        Term("d", "东京", "Đông Kinh"), Term("e", "官家", "quan gia"),
    ])
    res = translate_segments(plan_segments(text), CT2Translator(threads=4), beam=2, batch_size=32, glossary=index)
    hit = [s for s in res.segments if s.glossary_hits]
    lost = [s for s in hit if "placeholder_lost" in s.flags]
    retried = [s for s in hit if "retried" in s.flags]
    print(f"\n{len(hit)} câu có term · dịch lại {len(retried)} · vẫn mất {len(lost)}")
    assert hit and len(lost) <= max(1, len(hit) // 20)  # tối đa 5% câu có term còn hỏng
    assert all("Triệu Khải" in s.dst for s in hit if "a" in s.glossary_hits and not s.flags)
