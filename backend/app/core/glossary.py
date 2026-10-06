import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

_END = "\0"


def nfc(s: str) -> str:
    """Glossary luôn so khớp và lưu ở dạng NFC (NFD từ file / bàn phím khác không được làm lệch so sánh)."""
    return unicodedata.normalize("NFC", s)


@dataclass(frozen=True)
class Term:
    id: str
    src: str
    dst: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Match:
    start: int
    end: int
    term: Term


class GlossaryIndex:
    """BR-4.1: quét trái sang phải, tại mỗi vị trí lấy term dài nhất, các chỗ khớp không chồng nhau."""

    def __init__(self, terms: Iterable[Term]):
        self.terms = [t for t in terms if t.src]
        self._root: dict = {}
        for t in self.terms:
            node = self._root
            for ch in t.src:
                node = node.setdefault(ch, {})
            node[_END] = t

    def __bool__(self) -> bool:
        return bool(self.terms)

    def find(self, text: str) -> list[Match]:
        out: list[Match] = []
        i, n = 0, len(text)
        while i < n:
            node, j, best = self._root, i, None
            while j < n and text[j] in node:
                node = node[text[j]]
                j += 1
                if _END in node:
                    best = (j, node[_END])
            if best is None:
                i += 1
                continue
            out.append(Match(i, best[0], best[1]))
            i = best[0]
        return out

    def count(self, text: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.find(text):
            counts[m.term.id] = counts.get(m.term.id, 0) + 1
        return counts
