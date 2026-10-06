import unicodedata
from dataclasses import dataclass, field

from app.honorific import rules as R
from app.honorific.text import (
    DST_QUOTES, SRC_QUOTES, Span, apply_edits, count_tokens, dialogues, find_word, overlaps,
)

GENRE_PRIOR = {"xianxia": 0.3, "xuanhuan": 0.3, "urban": -0.3, "modern_war": -0.3, "romance": -0.3}
GENRE_ROUTE = {"xianxia": "ancient", "xuanhuan": "ancient", "urban": "modern", "modern_war": "modern", "romance": "modern"}


@dataclass(frozen=True)
class SegInput:
    src: str
    dst_raw: str
    src_protected: tuple[Span, ...] = ()
    dst_protected: tuple[Span, ...] = ()


@dataclass
class SegOut:
    dst: str
    edits: list[dict]


@dataclass
class Stats:
    applied: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def to_json(self) -> dict:
        return {"applied": dict(self.applied), "skipped": dict(self.skipped)}


def classify(src_text: str, genre: str) -> tuple[str, float]:
    m = R.markers()
    ancient = sum(src_text.count(w) for w in m["ancient"])
    modern = sum(src_text.count(w) for w in (*m["modern"], *m["military"]))
    if ancient + modern < 3:
        return GENRE_ROUTE.get(genre, "unknown"), 0.0
    score = (ancient - modern) / max(ancient + modern, 1) + GENRE_PRIOR.get(genre, 0.0)
    score = round(max(-1.0, min(1.0, score)), 3)
    route = "ancient" if score >= 0.4 else "modern" if score <= -0.4 else "mixed"
    return route, score


def active_layers(route: str, config: dict) -> set[str]:
    layers = set()
    if config.get("kinship") and route in ("ancient", "mixed"):
        layers.add("kinship")
    if config.get("pronoun") and route == "ancient":
        layers.add("pronoun")
    if config.get("modern_stable") and route == "modern":
        layers.add("modern_stable")
    return layers


class _Seg:
    """Tìm thay đổi trên bản thô gốc. `claimed`: vùng đích đã thuộc về một luật (hoặc glossary)."""

    def __init__(self, item: SegInput, stats: Stats):
        self.item, self.stats = item, stats
        self.dst = item.dst_raw
        self.claimed: list[Span] = list(item.dst_protected)
        self.edits: list[tuple[int, int, str, str, str]] = []
        self.phrases = [(s, e, p) for p in R.ALL_PHRASES for s, e in find_word(self.dst, p, 0, len(self.dst))]

    def _inside_foreign_phrase(self, span: Span, own: set[str]) -> bool:
        return any(s <= span[0] and span[1] <= e and (e - s) > (span[1] - span[0]) and p not in own
                   for s, e, p in self.phrases)

    def _others_forms(self, own_src: str, src_scope: Span, modern: bool) -> set[str]:
        """Dạng đích thuộc luật khác mà token nguồn của nó có mặt trong cùng vùng nguồn."""
        src = self.item.src
        present = count_tokens(src, *src_scope, R.all_rule_forms(modern).keys(), R.all_blockers(), self.item.src_protected)
        out: set[str] = set()
        for token in present:
            if token != own_src:
                out |= R.all_rule_forms(modern)[token]
        return out

    def match(self, rule: R.Rule, n: int, scope: Span, include_target: bool, src_scope: Span, modern: bool = False) -> list[Span] | None:
        """Mọi khớp không chồng nhau của dạng hợp lệ trong `scope`; chỉ nhận khi tổng đúng bằng `n`."""
        own = {*rule.forms, rule.target}
        others = self._others_forms(rule.src, src_scope, modern)
        forms = [f for f in rule.forms if f not in others]
        if include_target and rule.target:
            forms.append(rule.target)
        forms = sorted(set(forms), key=lambda f: (-len(f), f != rule.target))
        found: list[Span] = []
        for form in forms:
            for span in find_word(self.dst, form, *scope):
                if overlaps(span, self.claimed) or overlaps(span, found) or self._inside_foreign_phrase(span, own):
                    continue
                found.append(span)
        return sorted(found) if len(found) == n else None

    def apply_rule(self, rule: R.Rule, n: int, scope: Span, layer: str, *, include_target: bool, src_scope: Span) -> None:
        found = self.match(rule, n, scope, include_target, src_scope, layer == "modern_stable")
        if found is None:
            self.stats.skip("count_mismatch")
            return
        self.claimed.extend(found)
        for s, e in found:
            if self.dst[s:e].lower() != rule.target.lower():
                self.edits.append((s, e, rule.target, rule.rule, rule.src))
                self.stats.applied[layer] = self.stats.applied.get(layer, 0) + 1

    def dialogue_pairs(self) -> list[tuple[Span, Span]] | None:
        src_d = dialogues(self.item.src, SRC_QUOTES)
        dst_d = dialogues(self.dst, DST_QUOTES)
        if not src_d:
            return []
        if len(src_d) != len(dst_d):
            self.stats.skip("dialogue_mismatch")
            return None
        return list(zip(src_d, dst_d))

    def finish(self) -> SegOut:
        dst, edits = apply_edits(self.dst, self.edits)
        return SegOut(dst, edits)


def _kinship(seg: _Seg) -> None:
    rules, blockers = R.kinship()
    src = seg.item.src
    counts = count_tokens(src, 0, len(src), [r.src for r in rules], blockers, seg.item.src_protected)
    for r in sorted(rules, key=lambda r: -len(r.src)):
        if counts.get(r.src):
            seg.apply_rule(r, counts[r.src], (0, len(seg.dst)), "kinship", include_target=True, src_scope=(0, len(src)))


def _pronoun(seg: _Seg) -> None:
    src = seg.item.src
    counts = count_tokens(src, 0, len(src), [r.src for r in R.NARRATIVE], R.PRONOUN_BLOCKERS, seg.item.src_protected)
    for r in R.NARRATIVE:
        if counts.get(r.src):
            seg.apply_rule(r, counts[r.src], (0, len(seg.dst)), "pronoun", include_target=True, src_scope=(0, len(src)))
    pairs = seg.dialogue_pairs()
    for (ss, se), (ds, de) in pairs or []:
        part = src[ss:se]
        counts = count_tokens(src, ss, se, [r.src for r in R.DIALOGUE], (), seg.item.src_protected)
        for r in R.DIALOGUE:
            n = counts.get(r.src)
            if not n:
                continue
            rule = r
            if r.src == "我":
                blockers = R.MODERN_BLOCKERS
                exc = count_tokens(src, ss, se, [m for m, _ in R.RESPECT_EXCEPTIONS], blockers, seg.item.src_protected)
                exception = next((t for marker, t in R.RESPECT_EXCEPTIONS if marker in exc), None)
                if exception:
                    rule = r.with_target(exception)
                elif count_tokens(src, ss, se, R.RESPECT_MARKERS, blockers, seg.item.src_protected):
                    seg.stats.skip("respect")
                    continue
            seg.apply_rule(rule, n, (ds, de), "pronoun", include_target=True, src_scope=(ss, se))


def _modern(segs: list[_Seg]) -> None:
    carry: tuple[tuple[str, str | None], int] | None = None
    for k, seg in enumerate(segs):
        pairs = seg.dialogue_pairs()
        src = seg.item.src
        for (ss, se), (ds, de) in pairs or []:
            found = count_tokens(src, ss, se, list(R.MODERN_MARKERS), R.MODERN_BLOCKERS, seg.item.src_protected)
            roles = {R.MODERN_MARKERS[m] for m in found}
            if len(roles) > 1:
                seg.stats.skip("conflict")
                carry = None
                continue
            if roles:
                pair = roles.pop()
                carry = (pair, k + R.MODERN_CARRY_SEGMENTS)
            elif carry and k <= carry[1]:
                pair = carry[0]
            else:
                continue
            speaker, listener = pair
            counts = count_tokens(src, ss, se, ["我", "你"], ["我们", "咱们", "你们"], seg.item.src_protected)
            if counts.get("我"):
                rule = R.ME_MODERN.with_target(speaker, drop_forms=(listener or "",))
                seg.apply_rule(rule, counts["我"], (ds, de), "modern_stable", include_target=False, src_scope=(ss, se))
            if counts.get("你") and listener:
                rule = R.YOU_MODERN.with_target(listener, drop_forms=(speaker,))
                seg.apply_rule(rule, counts["你"], (ds, de), "modern_stable", include_target=False, src_scope=(ss, se))


def _nfc_spans(text: str, spans: tuple[Span, ...]) -> tuple[Span, ...]:
    return tuple((len(unicodedata.normalize("NFC", text[:s])), len(unicodedata.normalize("NFC", text[:e]))) for s, e in spans)


def _nfc(item: SegInput) -> SegInput:
    src, raw = unicodedata.normalize("NFC", item.src), unicodedata.normalize("NFC", item.dst_raw)
    if src == item.src and raw == item.dst_raw:
        return item
    return SegInput(src, raw, _nfc_spans(item.src, item.src_protected), _nfc_spans(item.dst_raw, item.dst_protected))


def apply_chapter(items: list[SegInput], route: str, config: dict) -> tuple[list[SegOut], Stats]:
    stats = Stats()
    layers = active_layers(route, config)
    segs = [_Seg(_nfc(item), stats) for item in items]
    if "kinship" in layers:
        for seg in segs:
            _kinship(seg)
    if "pronoun" in layers:
        for seg in segs:
            _pronoun(seg)
    if "modern_stable" in layers:
        _modern(segs)
    return [seg.finish() for seg in segs], stats


def preview(src: str, dst_raw: str, route: str, config: dict) -> dict:
    outs, stats = apply_chapter([SegInput(src, dst_raw)], route, config)
    return {"dst": outs[0].dst, "edits": outs[0].edits, "skipped": stats.skipped}
