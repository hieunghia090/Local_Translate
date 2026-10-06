import re

_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_URL_PREFIXES = ("http://", "https://", "www.")
_CLOSERS = "”’」』）)\"'"
_SENTENCE = re.compile(rf"[^。！？；!?;]*[。！？；!?;]+[{re.escape(_CLOSERS)}]*|[^。！？；!?;]+")
_CLAUSE = re.compile(rf"[^，、：,:]*[，、：,:]+[{re.escape(_CLOSERS)}]*|[^，、：,:]+")


def has_chinese(text: str) -> bool:
    return bool(_HAN.search(text))


def count_han(text: str) -> int:
    return len(_HAN.findall(text))


def is_meta_line(line: str) -> bool:
    t = line.strip()
    return not t or t.startswith(_URL_PREFIXES) or not has_chinese(t)


def _pieces(text: str, max_chars: int) -> list[str]:
    out: list[str] = []
    for sent in _SENTENCE.findall(text):
        if len(sent) <= max_chars:
            out.append(sent)
            continue
        for clause in _CLAUSE.findall(sent):
            if len(clause) <= max_chars:
                out.append(clause)
            else:
                out.extend(clause[i : i + max_chars] for i in range(0, len(clause), max_chars))
    return out


def split_long_line(text: str, max_chars: int = 250) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    merged: list[str] = []
    current = ""
    for piece in _pieces(text, max_chars):
        if len(current) + len(piece) <= max_chars:
            current += piece
        else:
            if current:
                merged.append(current)
            current = piece
    if current:
        merged.append(current)
    return merged
