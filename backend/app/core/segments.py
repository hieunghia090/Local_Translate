from dataclasses import dataclass

from app.core.lines import is_meta_line, split_long_line


@dataclass(frozen=True)
class SegmentPlan:
    idx: int
    src: str
    is_meta: bool
    parts: tuple[str, ...]


def plan_segments(text: str, max_chars: int = 250) -> list[SegmentPlan]:
    plans: list[SegmentPlan] = []
    for idx, line in enumerate(text.split("\n")):
        if is_meta_line(line):
            plans.append(SegmentPlan(idx, line, True, ()))
        else:
            plans.append(SegmentPlan(idx, line, False, tuple(split_long_line(line.strip(), max_chars))))
    return plans
