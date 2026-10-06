import time
import uuid

from app.ids import uuid7


def test_uuid7_version_and_variant():
    u = uuid7()
    assert u.version == 7
    assert u.variant == uuid.RFC_4122


def test_uuid7_sorts_by_creation_time():
    a = uuid7()
    time.sleep(0.002)
    b = uuid7()
    assert a < b
    assert str(a) < str(b)


def test_uuid7_unique():
    assert len({uuid7() for _ in range(10_000)}) == 10_000


def test_uuid7_monotonic_within_same_millisecond():
    ids = [uuid7() for _ in range(20_000)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
