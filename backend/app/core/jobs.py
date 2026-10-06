from collections.abc import Iterable

from app.core.segments import SegmentPlan

LINES_PER_STEP = 32  # số dòng mỗi bước: đơn vị lưu tạm, báo tiến độ và kiểm tra tạm dừng / huỷ
RISK_FLAGS = frozenset({"truncated", "placeholder_lost", "empty_output", "fallback_ct2", "residual_han", "glossary_miss"})
_OOM_MARKERS = ("out of memory", "bad_alloc", "cannot allocate memory")


def plan_steps(
    plans: list[SegmentPlan], done: set[int], lines_per_step: int = LINES_PER_STEP
) -> list[list[SegmentPlan]]:
    """Chia các dòng cần dịch mà chưa có kết quả thành từng bước."""
    pending = [p for p in plans if not p.is_meta and p.idx not in done]
    return [pending[i : i + lines_per_step] for i in range(0, len(pending), lines_per_step)]


def count_translatable(plans: list[SegmentPlan]) -> int:
    return sum(1 for p in plans if not p.is_meta)


def progress_pct(done: int, total: int) -> int:
    if total <= 0:
        return 100
    return min(100, done * 100 // total)


def chapter_status_after(flag_lists: Iterable[list[str]]) -> str:
    """BR-0.1: có câu mang cờ rủi ro thì chương cần soát."""
    return "needs_review" if any(RISK_FLAGS.intersection(flags) for flags in flag_lists) else "translated"


def is_out_of_memory(exc: BaseException) -> bool:
    return isinstance(exc, MemoryError) or any(m in str(exc).lower() for m in _OOM_MARKERS)
