import argparse
import sys
import time
from pathlib import Path

from app.core.pipeline import ChapterResult, TranslationCountMismatch, render, translate_segments
from app.core.segments import plan_segments
from app.core.textio import SourceDecodeError, UnsupportedEncodingError, decode_source
from app.core.translator import Translator


def translate_file(
    in_path: Path,
    out_path: Path,
    translator: Translator,
    *,
    beam: int = 2,
    batch_size: int = 8,
    encoding: str = "auto",
) -> ChapterResult:
    decoded = decode_source(Path(in_path).read_bytes(), encoding=encoding)
    result = translate_segments(plan_segments(decoded.text), translator, beam=beam, batch_size=batch_size)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(result), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    tf = sub.add_parser("translate-file", help="Dịch một file chương bằng HachimiMT-60")
    tf.add_argument("input", type=Path)
    tf.add_argument("output", type=Path)
    tf.add_argument("--beam", type=int, default=2)
    tf.add_argument("--batch", type=int, default=8)
    tf.add_argument("--threads", type=int, default=4)
    tf.add_argument("--encoding", default="auto", choices=["auto", "utf-8", "gbk", "big5"])
    args = parser.parse_args(argv)

    from app.core.ct2_translator import CT2Translator

    translator = CT2Translator(threads=args.threads)
    t0 = time.perf_counter()
    try:
        res = translate_file(
            args.input, args.output, translator, beam=args.beam, batch_size=args.batch, encoding=args.encoding
        )
    except (OSError, SourceDecodeError, UnsupportedEncodingError, TranslationCountMismatch) as e:
        print(f"Lỗi: {e}", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - t0
    translated = sum(1 for s in res.segments if not s.is_meta)
    flagged = sum(1 for s in res.segments if s.flags)
    print(
        f"{args.input.name}: {translated} dòng dịch, {flagged} dòng có cờ, "
        f"token {res.tokens_in}→{res.tokens_out}, {elapsed:.2f}s → {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
