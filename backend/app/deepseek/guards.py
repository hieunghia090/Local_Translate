"""Kiểm tra output DeepSeek (spec 08 mục 4.2). Hàm thuần, không gọi API."""
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.core.glossary import GlossaryIndex, nfc
from app.deepseek.client import OutputRejected

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_MARK = re.compile(r"^\s*⟦(\d+)⟧\s?(.*)$")
_PREAMBLE = re.compile(r"^\s*(?:đây là|dưới đây là|bản dịch\s*:|được rồi|vâng|ok\b|okay|sure|here is|here's)", re.I)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")
# G5: so với token VĂN BẢN của request (không tính prompt nền / glossary). Dịch 4,5 vì đo thực tế ra 3,3 lần token chữ Hán.
RATIO_LIMITS = {"translate": 4.5, "extract": 2.0, "review": 1.5}
MISSING_FULL_RETRY = 0.05  # G2


def han_stats(text: str) -> tuple[int, int]:
    """(số chữ Hán, số chữ cái). Chữ Hán cũng tính là chữ cái."""
    return len(_HAN.findall(text)), sum(1 for c in text if c.isalpha())


def check_not_chinese(text: str) -> None:
    """G1."""
    han, letters = han_stats(text)
    if letters >= 20 and han / letters >= 0.3:
        raise OutputRejected("han_output", "Bản dịch vẫn còn là tiếng Trung")


def strip_preamble(text: str) -> tuple[str, bool]:
    """G6: bỏ dòng lời dẫn nếu dòng có chữ đầu tiên không phải marker và khớp mẫu lời dẫn."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if _MARK.match(line) is None and _PREAMBLE.match(line):
            return "\n".join(lines[:i] + lines[i + 1:]), True
        break
    return text, False


def parse_marked(text: str) -> dict[int, str]:
    """Dòng ⟦idx⟧ mở một câu; dòng không marker nối vào câu đang mở; marker lặp bị bỏ (giữ lần đầu)."""
    out: dict[int, str] = {}
    current: int | None = None
    for line in text.split("\n"):
        m = _MARK.match(line)
        if m:
            idx = int(m.group(1))
            if idx in out:
                current = None
                continue
            current = idx
            out[idx] = m.group(2).strip()
        elif current is not None and line.strip():
            out[current] = f"{out[current]} {line.strip()}".strip()
    return out


def missing_markers(sent: list[int], got: dict[int, str]) -> list[int]:
    return [i for i in sent if not got.get(i, "").strip()]


def check_markers(sent: list[int], got: dict[int, str]) -> list[int]:
    """G2: thiếu từ 5% trở lên thì thử lại cả request; ít hơn thì trả danh sách dòng thiếu."""
    missing = missing_markers(sent, got)
    if sent and len(missing) / len(sent) >= MISSING_FULL_RETRY:
        raise OutputRejected("markers", f"Output thiếu {len(missing)}/{len(sent)} dòng")
    return missing


def has_residual_han(line: str) -> bool:
    """G3."""
    han, letters = han_stats(line)
    return han >= 4 and letters > 0 and han / letters >= 0.5


@dataclass(frozen=True)
class GlossaryCheck:
    dst: str
    autofixed: tuple[str, ...]
    missed: tuple[str, ...]


def check_glossary(src: str, dst: str, index: GlossaryIndex,
                   variants: Mapping[str, Sequence[str]] | None = None) -> GlossaryCheck:
    """G4: dòng nguồn có src_zh mà dòng dịch không có dst_vi. Thay alias (dài trước), rồi cách đọc Hán Việt của src_zh
    (G9: không phân biệt hoa thường, theo ranh giới từ) bằng dst_vi; không có biến thể nào thì miss."""
    dst = nfc(dst)  # so khớp theo NFC: dst_vi NFD không được làm output NFC bị "sửa" thành NFD
    autofixed: list[str] = []
    missed: list[str] = []
    seen: set[str] = set()
    for m in index.find(src) if index else []:
        t = m.term
        if t.id in seen:
            continue
        seen.add(t.id)
        t_dst = nfc(t.dst)
        if t_dst in dst:
            continue
        fixed = False
        for alias in sorted((nfc(a) for a in t.aliases if a and nfc(a) != t_dst), key=len, reverse=True):
            if alias in dst:
                dst, fixed = dst.replace(alias, t_dst), True
                break
        if not fixed:
            for v in sorted((nfc(v) for v in (variants or {}).get(t.id, ()) if v and nfc(v) != t_dst), key=len, reverse=True):
                pattern = re.compile(rf"(?<!\w){re.escape(v)}(?!\w)", re.IGNORECASE)
                if pattern.search(dst):
                    dst, fixed = pattern.sub(lambda _m, d=t_dst: d, dst), True
                    break
        (autofixed if fixed else missed).append(t.id)
    return GlossaryCheck(dst, tuple(autofixed), tuple(missed))


def ratio_abnormal(kind: str, text_tokens: int, completion_tokens: int) -> bool:
    """G5: token ra so với token văn bản của request."""
    return completion_tokens > RATIO_LIMITS[kind] * max(text_tokens, 1)


def clean_title(text: str) -> str:
    """BR-8.8: bỏ # và khoảng trắng."""
    return text.strip().lstrip("#").strip()


def parse_review_json(text: str) -> list[dict]:
    raw = _FENCE.sub("", text.strip())
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise OutputRejected("json", "JSON soát không hợp lệ") from e
    fixes = data.get("fixes") if isinstance(data, dict) else None
    if not isinstance(fixes, list):
        raise OutputRejected("json", "JSON soát thiếu mảng fixes")
    return [f for f in fixes if isinstance(f, dict)]
