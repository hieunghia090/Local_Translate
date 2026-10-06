from collections import defaultdict

from app.core.hanviet import syllable_key
from app.services.hanviet import SEED_PATH, parse_seed


def _table() -> dict[str, set[str]]:
    by: dict[str, set[str]] = defaultdict(set)
    for c, r, _ in parse_seed(SEED_PATH.read_text(encoding="utf-8")):
        by[c].add(syllable_key(r))
    return by


def test_ac_4_16_committed_seed_has_common_chars_without_nom():
    by = _table()
    for ch, reading in {"赵": "triệu", "苏": "tô", "雪": "tuyết", "藏": "tàng"}.items():
        assert syllable_key(reading) in by[ch], ch
    assert syllable_key("to") not in by["苏"]
    assert len(by) >= 8000


def test_seed_has_confirmed_rows():
    rows = parse_seed(SEED_PATH.read_text(encoding="utf-8"))
    assert sum(1 for _, _, s in rows if s == "confirmed") > 1000
