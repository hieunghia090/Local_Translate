import re
import unicodedata


def fold(text: str) -> str:
    """Bỏ dấu tiếng Việt và viết thường: 'Đại Tống' → 'dai tong'."""
    t = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def slugify(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-")
    return s[:max_len].rstrip("-")


def unique_slug(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"
