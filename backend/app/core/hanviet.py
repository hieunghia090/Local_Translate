"""Âm Hán Việt (spec 04 BR-4.15, mục 6a). Hàm thuần, không đụng DB."""
import itertools
import re
import unicodedata
from collections.abc import Mapping, Sequence

ELIGIBLE_CATEGORIES = ("character", "location", "organization", "realm")
NON_HAN_NAME_LANGS = ("ja", "foreign")
MAX_COMBINATIONS = 64  # giới hạn danh sách biến thể dùng ở G4
MAX_READING_LEN = 8
_TONES = {"̀": "2", "́": "1", "̉": "3", "̃": "4", "̣": "5"}
_VOWELS = set("aăâeêioôơuưy")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LETTERS_NFD = re.compile(r"^[a-zđ̀-ͯ]+$")
_SPLIT = re.compile(r"[\s\-‐-—]+")


def is_han(ch: str) -> bool:
    return bool(ch) and len(ch) == 1 and bool(_HAN.fullmatch(ch))


def han_chars(text: str) -> list[str] | None:
    chars = list(text.strip())
    return chars if chars and all(is_han(c) for c in chars) else None


def normalize_reading(raw) -> str | None:
    """Một âm tiết tiếng Việt, viết thường, NFC. Có dấu cách, chữ số hay ký hiệu thì không hợp lệ."""
    if not isinstance(raw, str):
        return None
    s = unicodedata.normalize("NFC", raw.strip().lower())
    if not s or len(s) > MAX_READING_LEN or not _LETTERS_NFD.match(unicodedata.normalize("NFD", s)):
        return None
    return s


def syllable_key(s: str) -> str:
    """Khoá so sánh một âm tiết: bỏ hoa thường, dấu thanh tách ra cuối (hoà = hòa), y cuối sau phụ âm = i (tỷ = tỉ)."""
    tone = ""
    base: list[str] = []
    for ch in unicodedata.normalize("NFD", s.strip().lower()):
        if ch in _TONES:
            tone = _TONES[ch]
        else:
            base.append(ch)
    b = unicodedata.normalize("NFC", "".join(base))
    if len(b) >= 2 and b.endswith("y") and (b[-2] not in _VOWELS or (b.startswith("qu") and len(b) == 3)):
        b = b[:-1] + "i"
    return b + tone


def syllables(text: str) -> list[str]:
    return [p for p in _SPLIT.split(unicodedata.normalize("NFC", text).strip()) if p]


def check_predictable(src: str, dst: str, category: str, name_lang: str | None,
                      readings: Mapping[str, Sequence[str]]) -> tuple[bool, frozenset[str]]:
    """BR-4.15. Kiểm từng vị trí: chữ thứ i có âm khớp âm tiết thứ i (tương đương xét mọi tổ hợp).
    Trả (predictable, các chữ chưa có âm). Chỉ báo chữ thiếu cho term thuộc 4 loại áp dụng."""
    if category not in ELIGIBLE_CATEGORIES or name_lang in NON_HAN_NAME_LANGS:
        return False, frozenset()
    chars = han_chars(src)
    if not chars:
        return False, frozenset()
    missing = frozenset(c for c in chars if not readings.get(c))
    if missing:
        return False, missing
    parts = syllables(dst)
    if len(parts) != len(chars):
        return False, frozenset()
    for c, part in zip(chars, parts):
        if syllable_key(part) not in {syllable_key(r) for r in readings[c]}:
            return False, frozenset()
    return True, frozenset()


def _title(s: str) -> str:
    return s[:1].upper() + s[1:]


def reading_variants(src: str, readings: Mapping[str, Sequence[str]], limit: int = MAX_COMBINATIONS) -> list[str]:
    """Các cách đọc Hán Việt của src_zh (Title Case), tối đa `limit` tổ hợp. Thiếu âm thì rỗng."""
    chars = han_chars(src)
    if not chars or any(not readings.get(c) for c in chars):
        return []
    out: list[str] = []
    for combo in itertools.product(*(readings[c] for c in chars)):
        out.append(" ".join(_title(r) for r in combo))
        if len(out) >= limit:
            break
    return out


def learn_pairs(src: str, dst: str, category: str, name_lang: str | None) -> list[tuple[str, str]]:
    """Mục 6a bước 4: số chữ bằng số âm tiết thì ghép từng chữ với từng âm."""
    if category not in ELIGIBLE_CATEGORIES or name_lang in NON_HAN_NAME_LANGS:
        return []
    chars, parts = han_chars(src), syllables(dst)
    if not chars or len(parts) != len(chars):
        return []
    out: list[tuple[str, str]] = []
    for c, p in zip(chars, parts):
        r = normalize_reading(p)
        if r is None:
            return []
        out.append((c, r))
    return out
