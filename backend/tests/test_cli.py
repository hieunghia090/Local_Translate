import time
from pathlib import Path

import pytest

from app.cli import translate_file
from app.config import ROOT
from app.core.translator import FakeTranslator

NOVEL_DIR = ROOT / "大宋有种--35466"


def test_translate_file_writes_utf8_and_keeps_meta(tmp_path: Path):
    src = tmp_path / "in.txt"
    src.write_bytes(
        b"\xef\xbb\xbf" + "第16章 你过河 我拆桥\r\n=====\r\nNguồn: https://a.b/c\r\n\r\n　　他走了。\r\n".encode("utf-8")
    )
    out = tmp_path / "out" / "vi.txt"
    res = translate_file(src, out, FakeTranslator())
    assert out.read_text(encoding="utf-8") == "VI<第16章 你过河 我拆桥>\n=====\nNguồn: https://a.b/c\n\n　　VI<他走了。>\n"
    assert res.model_id == "fake"


def test_translate_file_gbk_source(tmp_path: Path):
    src = tmp_path / "gbk.txt"
    src.write_bytes(("第一章 开始\n他走了。\n" * 30).encode("gbk"))
    out = tmp_path / "vi.txt"
    translate_file(src, out, FakeTranslator())
    assert out.read_text(encoding="utf-8").startswith("VI<第一章 开始>\nVI<他走了。>\n")


@pytest.mark.slow
def test_real_chapter_meets_nfr1(tmp_path: Path):
    from app.core.ct2_translator import CT2Translator

    first = sorted(NOVEL_DIR.glob("*.txt"))[0]
    translator = CT2Translator(threads=4)
    out = tmp_path / "vi.txt"
    t0 = time.perf_counter()
    res = translate_file(first, out, translator, beam=2, batch_size=32)
    elapsed = time.perf_counter() - t0
    src_lines = first.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    out_lines = out.read_text(encoding="utf-8").split("\n")
    assert len(out_lines) == len(src_lines)
    for s, o, seg in zip(src_lines, out_lines, res.segments):
        if seg.is_meta:
            assert o == s
    print(f"\nNFR-1: {first.name} · {elapsed:.2f}s · tokens {res.tokens_in}→{res.tokens_out}")
    assert elapsed < 5.0


def test_main_reports_decode_error_without_traceback(tmp_path: Path, monkeypatch, capsys):
    import app.core.ct2_translator as ct2
    from app.cli import main

    monkeypatch.setattr(ct2, "CT2Translator", lambda threads: FakeTranslator())
    src = tmp_path / "bad.txt"
    src.write_bytes(("第一章 开始\n" * 30).encode("gbk"))
    code = main(["translate-file", str(src), str(tmp_path / "o.txt"), "--encoding", "utf-8"])
    assert code == 1
    assert "Không đọc được file bằng utf-8" in capsys.readouterr().err
