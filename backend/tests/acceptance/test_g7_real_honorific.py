import time
from pathlib import Path

import pytest

from app.core.pipeline import translate_segments
from app.core.segments import plan_segments
from app.core.textio import decode_source
from app.honorific.engine import SegInput, apply_chapter, classify

SOURCE = Path(__file__).resolve().parents[3] / "大宋有种--35466"


@pytest.mark.slow
@pytest.mark.skipif(not SOURCE.is_dir(), reason="thiếu thư mục 大宋有种--35466")
def test_real_chapters_route_and_overhead():
    # BR-7.4, NFR-6, AC-7.10
    from app.core.ct2_translator import CT2Translator

    tr = CT2Translator(threads=4)
    cfg = {"kinship": True, "pronoun": True, "modern_stable": True}
    routes, t_translate, t_honor, edits = [], 0.0, 0.0, 0
    for path in sorted(SOURCE.glob("00[0-9][0-9] *.txt"))[:10]:
        text = decode_source(path.read_bytes()).text
        plans = plan_segments(text)
        t0 = time.perf_counter()
        res = translate_segments(plans, tr, beam=2, batch_size=32)
        t1 = time.perf_counter()
        route, score = classify(text, "modern_war")
        items = [SegInput(s.src, s.dst) for s in res.segments if not s.is_meta]
        outs, _ = apply_chapter(items, route, cfg)
        t2 = time.perf_counter()
        routes.append(route)
        t_translate += t1 - t0
        t_honor += t2 - t1
        edits += sum(len(o.edits) for o in outs)
    print(f"\nroute: {routes} · dịch {t_translate:.1f}s · xưng hô {t_honor * 1000:.0f}ms ({t_honor / t_translate:.1%}) · {edits} thay đổi")
    assert t_honor <= 0.10 * t_translate
    assert "unknown" not in routes
