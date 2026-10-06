import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _collect(*marker_args):
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *marker_args],
        cwd=BACKEND, capture_output=True, text=True, timeout=120,
    )
    return out.stdout + out.stderr


def test_db_marker_never_collects_live_test():
    text = _collect("-m", "db")
    assert "test_g8_live_deepseek" not in text


def test_live_test_is_collected_only_by_live_marker():
    text = _collect("-m", "live")
    assert "test_g8_live_deepseek" in text


def test_live_test_has_no_db_marker():
    import ast

    src = (BACKEND / "tests/acceptance/test_g8_live_deepseek.py").read_text()
    assert "mark.db" not in src
    assert "LT_LIVE" in ast.dump(ast.parse(src)) or "LT_LIVE" in src
