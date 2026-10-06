"""Chọn và nén glossary gửi cho DeepSeek (spec 08 mục 4.1a): L1 lọc theo chương, L2 dòng gọn, L3 bỏ term tự đoán được, L4 giới hạn."""
import re
from dataclasses import dataclass

from app.core.glossary import GlossaryIndex
from app.deepseek.estimate import text_tokens

NOTE_MAX = 60
_VOLATILE = re.compile(r"[（(]\s*(?:ch\.?|chương|chapter)\s*\d+\s*[)）]|[（(]\s*第\s*\d+\s*章\s*[)）]", re.I)


@dataclass(frozen=True)
class PromptTerm:
    id: str
    src: str
    dst: str
    note: str | None = None  # chỉ có khi term bật prompt_note
    predictable: bool = False  # L3 (BR-4.15)
    always_send: bool = False


@dataclass(frozen=True)
class Selection:
    lines: tuple[str, ...]
    matched: int
    sent: int
    truncated: int
    skipped_predictable: int
    tokens_est: int
    skipped_ids: tuple[str, ...] = ()  # term bị L3 bỏ, để G4 đếm miss

    def stats(self) -> dict:  # BR-8.3c
        return {"matched": self.matched, "sent": self.sent, "skipped_predictable": self.skipped_predictable,
                "truncated": self.truncated, "tokens_est": self.tokens_est}


EMPTY = Selection((), 0, 0, 0, 0, 0)


def clean_note(note: str | None) -> str:
    """L2: bỏ phần thay đổi theo chương như "(ch. 12)", gộp khoảng trắng, tối đa 60 ký tự."""
    if not note:
        return ""
    s = " ".join(_VOLATILE.sub("", note).split()).strip(" ,;·-")
    return s[:NOTE_MAX].rstrip()


def term_line(t: PromptTerm) -> str:
    note = clean_note(t.note)
    return f"{t.src}={t.dst} ({note})" if note else f"{t.src}={t.dst}"


def select_terms(text: str, index: GlossaryIndex, terms: dict[str, PromptTerm], max_terms: int = 120) -> Selection:
    if not index or not text:
        return EMPTY
    counts = index.count(text)  # L1: dài trước, không chồng nhau (BR-4.1)
    ids = [tid for tid in counts if tid in terms]
    skipped = tuple(sorted((tid for tid in ids if terms[tid].predictable and not terms[tid].always_send),
                           key=lambda tid: terms[tid].src))  # L3
    skip = set(skipped)
    candidates = [tid for tid in ids if tid not in skip]
    ranked = sorted(candidates, key=lambda tid: (-counts[tid], -len(terms[tid].src), terms[tid].src))  # L4
    keep = ranked[: max(0, max_terms)]
    lines = tuple(term_line(terms[tid]) for tid in sorted(keep, key=lambda tid: terms[tid].src))  # BR-8.3a
    return Selection(lines, len(ids), len(keep), len(candidates) - len(keep), len(skipped),
                     text_tokens("\n".join(lines)), skipped)
