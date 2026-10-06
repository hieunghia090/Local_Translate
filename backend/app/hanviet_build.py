"""`make hanviet`: dựng file seed âm Hán Việt (spec 04 mục 6a, bước 1–2; BR-4.19).

1. Danh sách chữ: 8.105 chữ của 通用规范汉字表 (trường kTGH của Unihan) cùng các chữ phồn thể tương ứng
   (kTraditionalVariant).
2. DeepSeek (deepseek-flash, JSON, tắt thinking) sinh âm theo lô 400 chữ. Kết quả từng lô lưu vào cache để chạy lại
   không tốn tiền lần nữa.
3. Đối chiếu kVietnamese của Unihan (Unihan chỉ ghi cho chữ phồn thể nên gộp cả âm của dạng phồn thể): âm có ở cả hai
   nguồn thì `confirmed`; âm chỉ có ở Unihan thì bỏ (có thể là âm Nôm, ví dụ 蘇 → "to").
4. Ghi data/hanviet_seed.tsv, in số token và chi phí."""
import argparse
import asyncio
import json
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import get_settings
from app.core.hanviet import is_han, normalize_reading, syllable_key
from app.deepseek import hanviet_ai
from app.deepseek.catalog import DEFAULT_MODELS
from app.deepseek.client import DeepSeekClient, Usage
from app.deepseek.estimate import cost_usd
from app.services.hanviet import CONFIDENCE, SEED_HEADER, SEED_PATH

UNIHAN_URL = "https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip"
UNIHAN_FILES = ("Unihan_OtherMappings.txt", "Unihan_Variants.txt", "Unihan_Readings.txt")


def cache_dir() -> Path:
    return get_settings().data_path / "cache"


def download_unihan(dest: Path, *, url: str = UNIHAN_URL, transport: httpx.BaseTransport | None = None) -> Path:
    """Tải Unihan.zip một lần vào cache; đã có thì dùng lại."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with httpx.Client(transport=transport, timeout=120.0, follow_redirects=True) as http:
        with http.stream("GET", url) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
    tmp.replace(dest)
    return dest


def _cp(token: str) -> str | None:
    """"U+8D75" → "赵". Bỏ phần đuôi kiểu "U+8D99<kMatthews" của bản Unihan cũ."""
    token = token.split("<", 1)[0].strip()
    if not token.startswith("U+"):
        return None
    try:
        return chr(int(token[2:], 16))
    except ValueError:
        return None


@dataclass
class Unihan:
    tgh: list[str]
    traditional: dict[str, list[str]]
    vietnamese: dict[str, list[str]]


def parse_unihan(texts: dict[str, str]) -> Unihan:
    tgh: list[str] = []
    trad: dict[str, list[str]] = {}
    viet: dict[str, list[str]] = {}
    for text in texts.values():
        for line in text.splitlines():
            if not line.startswith("U+"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            ch, field, value = _cp(parts[0]), parts[1], parts[2]
            if ch is None:
                continue
            if field == "kTGH":
                tgh.append(ch)
            elif field == "kTraditionalVariant":
                trad[ch] = [t for t in (_cp(v) for v in value.split()) if t and t != ch]
            elif field == "kVietnamese":
                viet[ch] = [r for r in (normalize_reading(v) for v in value.split()) if r]
    return Unihan(list(dict.fromkeys(tgh)), trad, viet)


def read_unihan_zip(path: Path) -> Unihan:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        return parse_unihan({n: z.read(n).decode("utf-8") for n in UNIHAN_FILES if n in names})


def char_list(u: Unihan) -> list[str]:
    out = list(u.tgh)
    for c in u.tgh:
        out.extend(u.traditional.get(c, []))
    return [c for c in dict.fromkeys(out) if is_han(c)]


def unihan_readings(u: Unihan, ch: str) -> list[str]:
    out = list(u.vietnamese.get(ch, []))
    for t in u.traditional.get(ch, []):
        out.extend(u.vietnamese.get(t, []))
    return out


def cross_check(ai: dict[str, list[str]], u: Unihan) -> list[tuple[str, str, str, int]]:
    rows: list[tuple[str, str, str, int]] = []
    for ch in sorted(ai, key=ord):
        unihan_keys = {syllable_key(r) for r in unihan_readings(u, ch)}
        for r in ai[ch]:
            source = "confirmed" if syllable_key(r) in unihan_keys else "ai"
            rows.append((ch, r, source, CONFIDENCE[source]))
    return rows


def write_seed(rows: list[tuple[str, str, str, int]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(SEED_HEADER)] + [f"{c}\t{r}\t{s}\t{n}" for c, r, s, n in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass
class BuildResult:
    chars: int
    readings: int
    confirmed: int
    usage: Usage
    cost_usd: float
    empty: list[str]


async def build(client: DeepSeekClient, unihan: Unihan, out: Path, *, prices: dict, model: str = hanviet_ai.MODEL,
                progress_path: Path | None = None, limit: int | None = None, redo: str = "",
                echo: Callable[[str], None] = print) -> BuildResult:
    chars = char_list(unihan)[:limit] if limit else char_list(unihan)
    done: dict[str, list[str]] = {}
    if progress_path and progress_path.exists():
        done = json.loads(progress_path.read_text(encoding="utf-8"))
    for c in redo:
        done.pop(c, None)
    todo = [c for c in chars if c not in done]
    usage = Usage()
    for i, part in enumerate(hanviet_ai.batches(todo), start=1):
        found, completion = await hanviet_ai.ask_readings(client, part, model=model)
        usage = usage + completion.usage
        for c in part:
            done[c] = found.get(c, [])
        if progress_path:
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            progress_path.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
        echo(f"Lô {i}: {len(part)} chữ · {completion.usage.prompt_tokens} → {completion.usage.completion_tokens} token")
    rows = cross_check({c: done[c] for c in chars if done.get(c)}, unihan)
    write_seed(rows, out)
    cost = cost_usd(tokens_in=usage.prompt_tokens, tokens_in_cached=usage.prompt_cache_hit_tokens,
                    tokens_out=usage.completion_tokens, price_in=prices["price_in_per_mtok"],
                    price_cached=prices["price_in_cached_per_mtok"], price_out=prices["price_out_per_mtok"])
    return BuildResult(len(chars), len(rows), sum(1 for r in rows if r[2] == "confirmed"), usage, cost,
                       [c for c in chars if not done.get(c)])


async def _prices(model: str) -> dict:
    """Giá trong bảng ai_models (người dùng có thể đã sửa); DB chưa chạy thì dùng giá mẫu của catalog."""
    fallback = next((m for m in DEFAULT_MODELS if m["id"] == model), DEFAULT_MODELS[-1])
    try:
        from app.db import get_sessionmaker
        from app.services.ai_cost import get_model

        async with get_sessionmaker()() as s:
            m = await get_model(s, model)
        if m is not None:
            return {"price_in_per_mtok": float(m.price_in_per_mtok),
                    "price_in_cached_per_mtok": float(m.price_in_cached_per_mtok),
                    "price_out_per_mtok": float(m.price_out_per_mtok)}
    except Exception:
        pass
    return fallback


async def _main(args) -> int:
    zip_path = args.unihan or download_unihan(cache_dir() / "Unihan.zip")
    unihan = read_unihan_zip(zip_path)
    print(f"Unihan: {len(unihan.tgh)} chữ kTGH, {len(unihan.vietnamese)} chữ có kVietnamese")
    prices = await _prices(args.model)
    r = await build(DeepSeekClient.from_settings(), unihan, args.out, prices=prices, model=args.model,
                    progress_path=cache_dir() / f"hanviet_ai_{args.model}.json", limit=args.limit, redo=args.redo)
    u = r.usage
    print(f"{r.chars} chữ · {r.readings} âm ({r.confirmed} confirmed) · {u.prompt_tokens} token vào "
          f"({u.prompt_cache_hit_tokens} trúng cache) · {u.completion_tokens} token ra · ${r.cost_usd:.4f} · "
          f"{len(r.empty)} chữ không có âm → {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dựng data/hanviet_seed.tsv bằng DeepSeek + Unihan")
    ap.add_argument("--out", type=Path, default=SEED_PATH)
    ap.add_argument("--unihan", type=Path, default=None, help="Unihan.zip có sẵn (mặc định tải một lần vào cache)")
    ap.add_argument("--model", default=hanviet_ai.MODEL)
    ap.add_argument("--limit", type=int, default=None, help="Chỉ lấy N chữ đầu (chạy thử)")
    ap.add_argument("--redo", default="", help="Hỏi lại DeepSeek cho các chữ này (bỏ khỏi cache)")
    args = ap.parse_args(argv)
    if not get_settings().deepseek_api_key.strip():
        print("Chưa có DEEPSEEK_API_KEY trong .env", file=sys.stderr)
        return 2
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
