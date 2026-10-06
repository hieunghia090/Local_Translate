import re

_AD_WORDS = ("求", "订阅", "月票", "收藏", "推荐", "打赏", "上架", "加更", "爆更", "票")
_TRAILING_GROUP = re.compile(r"\s*[（(【\[]([^（）()【】\[\]]*)[）)】\]]\s*$")
_MD_HEADING = re.compile(r"^\s*#{1,3}\s+")
_NUMBERED = re.compile(
    r"^\s*(?:第\s*([0-9０-９零〇一二两三四五六七八九十百千]+)\s*([章回节卷])|Chương\s*(\d+))\s*[:：.、\-—]?\s*",
    re.IGNORECASE,
)
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
AUTHOR_NOTE_WORDS = ("作者的话", "感言", "请假", "上架")


def strip_ads(title: str) -> str:
    """Bỏ các cụm quảng cáo trong ngoặc ở cuối tiêu đề: （求收藏，求推荐）, （明天就要上架啦！）…"""
    t = title.strip()
    while (m := _TRAILING_GROUP.search(t)) and any(w in m.group(1) for w in _AD_WORDS):
        t = t[: m.start()].rstrip()
    return t


def parse_cn_number(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    if not any(c in _CN_UNITS for c in s):  # viết từng chữ số: 一二三 → 123
        return int("".join(str(_CN_DIGITS[c]) for c in s)) if all(c in _CN_DIGITS for c in s) else None
    total, digit = 0, None
    for c in s:
        if c in _CN_DIGITS:
            digit = _CN_DIGITS[c]
        elif c in _CN_UNITS:
            total += (1 if digit is None else digit) * _CN_UNITS[c]
            digit = None
        else:
            return None
    return total + (digit or 0)


def split_heading(title: str) -> tuple[int | None, str]:
    """'第16章 你过河' → (16, '你过河'). Tiêu đề quyển (卷) giữ nguyên, không lấy số."""
    t = _MD_HEADING.sub("", title.strip(), count=1)
    m = _NUMBERED.match(t)
    if not m or m.group(2) == "卷":
        return None, t.strip()
    if m.group(3):
        return int(m.group(3)), t[m.end():].strip()
    return parse_cn_number(m.group(1)), t[m.end():].strip()


def translation_source(title: str) -> str:
    """Phần tiêu đề đưa vào model: đã bỏ quảng cáo và tiền tố số chương."""
    return split_heading(strip_ads(title))[1]


def default_title_vi(number: int | None, fallback_no: int, machine: str | None) -> str:
    n = number if number is not None else fallback_no
    return f"Chương {n}: {machine}" if machine else f"Chương {n}"


def is_author_note(title: str) -> bool:
    cleaned = strip_ads(title)
    return any(w in cleaned for w in AUTHOR_NOTE_WORDS)
