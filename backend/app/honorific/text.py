import re
from functools import lru_cache

Span = tuple[int, int]
SRC_QUOTES = (("“", "”"), ("「", "」"))
DST_QUOTES = (("“", "”"), ('"', '"'))


def overlaps(a: Span, spans) -> bool:
    return any(a[0] < e and s < a[1] for s, e in spans)


def count_tokens(text: str, start: int, end: int, tokens, blockers=(), protected=()) -> dict[str, int]:
    """Đếm token nguồn trong [start, end), ghép dài nhất trước, bỏ qua vùng được bảo vệ (glossary)."""
    vocab = sorted(set(tokens) | set(blockers), key=len, reverse=True)
    wanted = set(tokens)
    counts: dict[str, int] = {}
    i = start
    while i < end:
        for t in vocab:
            if text.startswith(t, i) and i + len(t) <= end:
                if t in wanted and not overlaps((i, i + len(t)), protected):
                    counts[t] = counts.get(t, 0) + 1
                i += len(t)
                break
        else:
            i += 1
    return counts


@lru_cache(maxsize=512)
def _word(word: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.IGNORECASE)


def find_word(text: str, word: str, start: int, end: int) -> list[Span]:
    return [(m.start(), m.end()) for m in _word(word).finditer(text, start, end)]


def dialogues(text: str, pairs) -> list[Span]:
    """Phần bên trong các cặp ngoặc thoại, theo thứ tự. Ngoặc mở không có ngoặc đóng: dừng."""
    spans: list[Span] = []
    i = 0
    while i < len(text):
        for o, c in pairs:
            if text.startswith(o, i):
                j = text.find(c, i + len(o))
                if j < 0:
                    return spans
                spans.append((i + len(o), j))
                i = j + len(c)
                break
        else:
            i += 1
    return spans


def match_case(original: str, replacement: str) -> str:
    return replacement[:1].upper() + replacement[1:] if original[:1].isupper() else replacement


def apply_edits(text: str, edits: list[tuple[int, int, str, str, str]]) -> tuple[str, list[dict]]:
    """edits: (start, end, to, rule, src_token) trên `text`, không chồng nhau. Trả bản mới và edits với offset trên bản mới."""
    out: list[str] = []
    recorded: list[dict] = []
    pos = delta = 0
    for s, e, to, rule, token in sorted(edits):
        original = text[s:e]
        repl = match_case(original, to)
        out.append(text[pos:s])
        recorded.append({"from": original, "to": repl, "rule": rule, "offset": s + delta, "src_token": token})
        out.append(repl)
        delta += len(repl) - (e - s)
        pos = e
    out.append(text[pos:])
    return "".join(out), recorded
