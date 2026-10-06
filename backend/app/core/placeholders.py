import re
from dataclasses import dataclass

from app.core.glossary import GlossaryIndex, Term

# Đo 2026-10-04: HachimiMT giữ nguyên tên giả kiểu này 97–100% (xem spec 04 BR-4.2).
PSEUDO_NAMES = (
    "Zorvak", "Pelmin", "Kastor", "Varnel", "Dumrek", "Solvan", "Merkat", "Tolvin", "Gavrun", "Lempik",
    "Horvat", "Basnik", "Fendor", "Ravlik", "Corvex", "Nalvor", "Wistam", "Jorbek", "Yevran", "Quilmo",
)


@dataclass(frozen=True)
class Masked:
    text: str
    slots: tuple[tuple[str, Term], ...]  # (tên giả, term)
    hits: tuple[str, ...]  # id các term khớp, theo thứ tự gặp, không lặp


def mask(text: str, index: GlossaryIndex) -> Masked:
    matches = index.find(text)
    if not matches:
        return Masked(text, (), ())
    names = [n for n in PSEUDO_NAMES if n not in text]
    parts: list[str] = []
    slots: list[tuple[str, Term]] = []
    pos = 0
    for m, name in zip(matches, names):  # quá 20 chỗ khớp: phần dư để model tự dịch
        parts.append(text[pos : m.start])
        parts.append(name)
        slots.append((name, m.term))
        pos = m.end
    parts.append(text[pos:])
    hits = tuple(dict.fromkeys(m.term.id for m in matches))
    return Masked("".join(parts), tuple(slots), hits)


def unmask(output: str, slots: tuple[tuple[str, Term], ...]) -> str | None:
    """Thay tên giả bằng dst_vi. None nếu có tên giả bị mất, bị đổi hoặc bị lặp (BR-4.3)."""
    if any(output.count(name) != 1 for name, _ in slots):
        return None
    if not slots:
        return output
    by_name = {name: term.dst for name, term in slots}
    pattern = "|".join(re.escape(n) for n in sorted(by_name, key=len, reverse=True))
    return re.sub(pattern, lambda m: by_name[m.group(0)], output)


# \w Unicode (chữ Việt tính là chữ), trừ chữ Hán: bản dịch thô có thể dính liền chữ Hán chưa dịch.
_WORD = r"[^\W\u3400-\u9fff]"


def _whole(text: str) -> str:
    return rf"(?<!{_WORD}){re.escape(text)}(?!{_WORD})"


def repair_with_aliases(output: str, terms: list[Term]) -> tuple[str, bool]:
    """Bản dịch thô (không placeholder): đổi alias thành dst_vi. ok khi mọi term đều có dst_vi nguyên từ trong kết quả."""
    ok = True
    for term in dict.fromkeys(terms):
        aliases = sorted({a for a in term.aliases if a and a != term.dst}, key=len, reverse=True)
        if aliases:
            # dst_vi đứng trước: đoạn đã là dst_vi được giữ nguyên, không bị thay lần nữa
            pattern = re.compile(f"(?P<dst>{_whole(term.dst)})|(?:{'|'.join(_whole(a) for a in aliases)})")
            output = pattern.sub(lambda m: m.group("dst") or term.dst, output)
        if not re.search(_whole(term.dst), output):
            ok = False
    return output, ok
