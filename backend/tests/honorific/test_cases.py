from pathlib import Path

import pytest
import yaml

from app.honorific.engine import SegInput, apply_chapter

CASES = yaml.safe_load((Path(__file__).parent / "cases.yaml").read_text(encoding="utf-8"))


def ranges(text: str, needles: list[str]) -> tuple[tuple[int, int], ...]:
    out = []
    for n in needles:
        i = text.find(n)
        assert i >= 0, f"không thấy {n!r} trong {text!r}"
        out.append((i, i + len(n)))
    return tuple(out)


def test_case_count_and_groups():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)) >= 60
    groups = {p: sum(1 for i in ids if i[0] == p) for p in "kpemxn"}
    assert groups["k"] + groups["p"] + groups["e"] >= 20 and groups["m"] >= 15 and groups["x"] >= 10 and groups["n"] >= 15


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(case):
    config = {layer: True for layer in case["layers"]}
    segs = case.get("segments") or [{"src": case["src"], "raw": case["raw"], "expected": case["expected"]}]
    items = [
        SegInput(s["src"], s["raw"], ranges(s["src"], case.get("protect_src", [])), ranges(s["raw"], case.get("protect_dst", [])))
        for s in segs
    ]
    outs, stats = apply_chapter(items, case["route"], config)
    assert [o.dst for o in outs] == [s["expected"] for s in segs]
    for out in outs:  # mọi thay đổi đều đúng chỗ trong bản cuối
        for e in out.edits:
            assert out.dst[e["offset"] : e["offset"] + len(e["to"])] == e["to"]
    for reason, n in case.get("skipped", {}).items():
        assert stats.skipped.get(reason, 0) == n, (reason, stats.skipped)
