import pytest

from app.services.library import book_state, progress_pct


@pytest.mark.parametrize(
    "total,done,pct",
    [(0, 0, 0), (707, 106, 15), (120, 120, 100), (1000, 999, 99), (3, 1, 33), (2, 1, 50), (8, 1, 13)],
)
def test_progress_pct(total, done, pct):
    # BR-1.1; chưa xong hết thì không hiện 100%
    assert progress_pct(total, done) == pct


@pytest.mark.parametrize(
    "total,done,state",
    [(0, 0, "not_started"), (10, 0, "not_started"), (10, 3, "in_progress"), (10, 10, "completed")],
)
def test_book_state(total, done, state):
    assert book_state(total, done) == state
