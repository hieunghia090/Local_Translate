import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_seq = 0


def uuid7() -> uuid.UUID:
    """UUID v7 (RFC 9562), tăng dần trong một process: 12 bit sau mili-giây là bộ đếm."""
    global _last_ms, _seq
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _last_ms:
            ms = _last_ms
            _seq += 1
            if _seq > 0xFFF:  # hết bộ đếm trong mili-giây này: mượn mili-giây kế tiếp
                ms += 1
                _seq = 0
        else:
            _seq = int.from_bytes(os.urandom(2), "big") & 0x3FF  # bắt đầu thấp, chừa chỗ để đếm
        _last_ms = ms
        seq = _seq
    rand = int.from_bytes(os.urandom(8), "big") & (2**62 - 1)
    value = (ms & 0xFFFF_FFFF_FFFF) << 80 | 0x7 << 76 | seq << 64 | 0b10 << 62 | rand
    return uuid.UUID(int=value)
