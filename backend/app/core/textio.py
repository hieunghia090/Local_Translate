from dataclasses import dataclass

from charset_normalizer import from_bytes

_BOM = b"\xef\xbb\xbf"
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_REPLACEMENT = "�"
_CODEC = {"utf-8": "utf-8", "gbk": "gb18030", "gb2312": "gb18030", "gb18030": "gb18030", "big5": "big5", "utf-16": "utf-16"}


@dataclass(frozen=True)
class DecodeResult:
    text: str
    encoding: str
    replacement_ratio: float


class UnsupportedEncodingError(ValueError):
    def __init__(self, encoding: str):
        super().__init__(f"Không hỗ trợ mã hoá {encoding!r}")
        self.encoding = encoding


class SourceDecodeError(ValueError):
    def __init__(self, encoding: str, ratio: float):
        super().__init__(f"Không đọc được file bằng {encoding}: {ratio:.1%} ký tự lỗi")
        self.encoding, self.ratio = encoding, ratio


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _detect(data: bytes) -> str:
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    best = from_bytes(data, cp_isolation=["gb18030", "big5"]).best()
    return best.encoding if best else "gb18030"


def decode_source(data: bytes, encoding: str = "auto", max_replacement_ratio: float = 0.01) -> DecodeResult:
    if encoding != "auto" and encoding not in _CODEC:
        raise UnsupportedEncodingError(encoding)
    if encoding == "auto" and data.startswith(_UTF16_BOMS):
        encoding = "utf-16"  # codec utf-16 tự đọc và bỏ BOM
    if data.startswith(_BOM):
        data = data[len(_BOM):]
        if encoding == "auto":
            encoding = "utf-8"
    if encoding == "auto":
        encoding = _detect(data)
    text = data.decode(_CODEC.get(encoding, encoding), errors="replace")
    ratio = text.count(_REPLACEMENT) / len(text) if text else 0.0
    if ratio > max_replacement_ratio:
        raise SourceDecodeError(encoding, ratio)
    return DecodeResult(_normalize(text), encoding, ratio)
