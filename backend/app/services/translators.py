import threading

from app.core.translator import Translator

_lock = threading.Lock()
_instance: Translator | None = None


def default_translator_factory() -> Translator:
    """HachimiMT dùng chung cho cả app, chỉ nạp ở lần gọi đầu (NFR-4)."""
    global _instance
    with _lock:
        if _instance is None:
            from app.config import get_settings
            from app.core.ct2_translator import CT2Translator

            s = get_settings()

            _instance = CT2Translator(threads=s.ct2_intra_threads, inter_threads=s.ct2_inter_threads)
        return _instance
