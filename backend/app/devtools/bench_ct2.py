"""Benchmark CTranslate2 (HachimiMT) throughput. Usage: python -m app.devtools.bench_ct2 [--out f.json]
Each config runs in its own subprocess (clean peak RSS)."""
import argparse, json, resource, subprocess, sys, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "Output" / "compare" / "source"
BEAM = 2


def load_segments(limit: int = 800) -> list[str]:
    segs: list[str] = []
    for i in range(1, 11):
        segs += [l.strip() for l in (SRC / f"ch{i:02d}.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    return segs[:limit]


def run_one(intra: int, inter: int, batch: int, btype: str, n: int, outfile: str | None) -> dict:
    import ctranslate2
    from app.core.ct2_translator import CT2Translator, CT2_SUBDIR

    t = CT2Translator(threads=intra)
    from huggingface_hub import snapshot_download
    from app.core.ct2_translator import HACHIMI_REPO
    path = Path(snapshot_download(HACHIMI_REPO)) / CT2_SUBDIR
    t._tr = ctranslate2.Translator(str(path), device="cpu", compute_type="int8_float32",
                                   intra_threads=intra, inter_threads=inter)
    segs = load_segments(n)
    kw = {"batch_size": batch}
    if btype == "tokens":
        # translate() uses examples; call the engine directly for token batching
        def run(chunk):
            enc = [t._encode(s)[0] for s in chunk]
            toks = [t._tok.convert_ids_to_tokens(x) for x in enc]
            res = t._tr.translate_batch(toks, batch_type="tokens", max_batch_size=batch, beam_size=BEAM,
                                        repetition_penalty=1.2, no_repeat_ngram_size=2)
            return [t._tok.decode(t._tok.convert_tokens_to_ids(r.hypotheses[0]), skip_special_tokens=True) for r in res]
    else:
        def run(chunk):
            return t.translate(chunk, beam=BEAM, **kw).outputs
    run(segs[:16])  # warm-up
    t0 = time.perf_counter()
    if inter == 1:
        out = run(segs)
    else:
        half = len(segs) // 2
        parts: list = [None, None]
        def w(k, c):
            parts[k] = run(c)
        ths = [threading.Thread(target=w, args=(0, segs[:half])), threading.Thread(target=w, args=(1, segs[half:]))]
        [x.start() for x in ths]; [x.join() for x in ths]
        out = parts[0] + parts[1]
    dt = time.perf_counter() - t0
    if outfile:
        Path(outfile).write_text(json.dumps(out, ensure_ascii=False))
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024  # macOS: bytes -> MB
    return {"intra": intra, "inter": inter, "batch": batch, "btype": btype, "n": len(segs),
            "secs": round(dt, 2), "seg_s": round(len(segs) / dt, 2), "peak_rss_mb": round(rss)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--one", nargs=4, metavar=("INTRA", "INTER", "BATCH", "BTYPE"))
    ap.add_argument("--n", type=int, default=800)
    ap.add_argument("--dump")
    a = ap.parse_args()
    if a.one:
        i, j, b, bt = a.one
        print(json.dumps(run_one(int(i), int(j), int(b), bt, a.n, a.dump)))
    else:
        print("run configs via --one INTRA INTER BATCH examples|tokens")
