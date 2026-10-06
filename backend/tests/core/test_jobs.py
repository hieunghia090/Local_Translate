import pytest

from app.core.jobs import chapter_status_after, count_translatable, is_out_of_memory, plan_steps, progress_pct
from app.core.segments import plan_segments

TEXT = "第1章 甲\n=====\n\n一句。\n二句。\n三句。\n四句。\n五句。"


def test_plan_steps_skips_meta_and_done_lines():
    plans = plan_segments(TEXT)
    steps = plan_steps(plans, done=set(), lines_per_step=2)
    assert [[p.idx for p in s] for s in steps] == [[0, 3], [4, 5], [6, 7]]
    resumed = plan_steps(plans, done={0, 3, 4}, lines_per_step=2)
    assert [[p.idx for p in s] for s in resumed] == [[5, 6], [7]]


def test_plan_steps_empty_when_all_done_or_meta():
    plans = plan_segments("=====\n\nNguồn: http://x")
    assert plan_steps(plans, done=set()) == []
    assert count_translatable(plans) == 0


def test_count_translatable():
    assert count_translatable(plan_segments(TEXT)) == 6


@pytest.mark.parametrize("done,total,pct", [(0, 6, 0), (2, 6, 33), (6, 6, 100), (0, 0, 100), (7, 6, 100)])
def test_progress_pct(done, total, pct):
    assert progress_pct(done, total) == pct


def test_chapter_status_after():
    assert chapter_status_after([[], ["retried"]]) == "translated"
    assert chapter_status_after([[], ["truncated"]]) == "needs_review"
    assert chapter_status_after([["placeholder_lost"]]) == "needs_review"
    assert chapter_status_after([["empty_output"]]) == "needs_review"
    assert chapter_status_after([]) == "translated"


@pytest.mark.parametrize(
    "exc,expected",
    [
        (MemoryError(), True),
        (RuntimeError("CUDA out of memory"), True),
        (RuntimeError("std::bad_alloc"), True),
        (RuntimeError("Cannot allocate memory"), True),
        (RuntimeError("model file missing"), False),
        (ValueError("x"), False),
    ],
)
def test_is_out_of_memory(exc, expected):
    assert is_out_of_memory(exc) is expected
