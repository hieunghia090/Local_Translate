from dataclasses import dataclass, field

from app.core.glossary import GlossaryIndex
from app.core.lines import has_chinese
from app.core.placeholders import Masked, mask, repair_with_aliases, unmask
from app.core.segments import SegmentPlan
from app.core.translator import Translator


@dataclass
class SegmentResult:
    idx: int
    src: str
    is_meta: bool
    dst: str
    flags: list[str] = field(default_factory=list)
    glossary_hits: list[str] = field(default_factory=list)


@dataclass
class ChapterResult:
    segments: list[SegmentResult]
    tokens_in: int
    tokens_out: int
    model_id: str


class TranslationCountMismatch(RuntimeError):
    def __init__(self, expected: int, got: int):
        super().__init__(f"Model trả {got} câu, cần {expected} câu")
        self.expected, self.got = expected, got


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _run(translator: Translator, texts: list[str], beam: int, batch_size: int) -> tuple[list[str], list[bool], int, int]:
    if not texts:
        return [], [], 0, 0
    batch = translator.translate(texts, beam=beam, batch_size=batch_size)
    for got in (len(batch.outputs), len(batch.truncated)):
        if got != len(texts):
            raise TranslationCountMismatch(len(texts), got)
    return list(batch.outputs), list(batch.truncated), batch.tokens_in, batch.tokens_out


def translate_segments(
    plans: list[SegmentPlan],
    translator: Translator,
    *,
    beam: int = 2,
    batch_size: int = 8,
    glossary: GlossaryIndex | None = None,
) -> ChapterResult:
    # Mảnh không có chữ Hán (vd. dấu câu bị cắt cứng) giữ nguyên, không gửi model.
    owners = [(plan.idx, part) for plan in plans for part in plan.parts if has_chinese(part)]
    masked = [mask(part, glossary) if glossary else Masked(part, (), ()) for _, part in owners]
    outputs, truncated, tokens_in, tokens_out = _run(translator, [m.text for m in masked], beam, batch_size)

    part_flags: list[set[str]] = [set() for _ in owners]
    retry: list[int] = []
    for k, (m, out) in enumerate(zip(masked, outputs)):
        if m.slots:
            restored = unmask(out, m.slots)
            if restored is None:
                retry.append(k)
            else:
                outputs[k] = restored
    if retry:  # BR-4.3: dịch lại câu gốc không dùng placeholder
        r_out, r_trunc, t_in, t_out = _run(translator, [owners[k][1] for k in retry], beam, batch_size)
        tokens_in, tokens_out = tokens_in + t_in, tokens_out + t_out
        for k, out, trunc in zip(retry, r_out, r_trunc):
            fixed, ok = repair_with_aliases(out, [term for _, term in masked[k].slots])
            outputs[k], truncated[k] = fixed, trunc
            part_flags[k].add("retried")
            if not ok:
                part_flags[k].add("placeholder_lost")

    translated = iter(range(len(owners)))
    segments: list[SegmentResult] = []
    for plan in plans:
        if plan.is_meta:
            segments.append(SegmentResult(plan.idx, plan.src, True, plan.src))
            continue
        pieces: list[tuple[str, bool]] = []
        flags: set[str] = set()
        hits: list[str] = []
        for part in plan.parts:
            if not has_chinese(part):
                pieces.append((part, False))
                continue
            k = next(translated)
            pieces.append((outputs[k], truncated[k]))
            flags |= part_flags[k]
            hits.extend(h for h in masked[k].hits if h not in hits)
        dst = _indent(plan.src) + " ".join(o.strip() for o, _ in pieces if o.strip())
        if any(t for _, t in pieces):
            flags.add("truncated")
        if any(not o.strip() for o, _ in pieces):
            flags.add("empty_output")
        order = ["truncated", "empty_output", "retried", "placeholder_lost"]
        segments.append(SegmentResult(plan.idx, plan.src, False, dst, [f for f in order if f in flags], hits))
    return ChapterResult(segments, tokens_in, tokens_out, translator.model_id)


def render(result: ChapterResult) -> str:
    return "\n".join(s.dst for s in result.segments)
