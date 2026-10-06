import pytest

from app.core.textio import SourceDecodeError, decode_source

SIMPLIFIED = "第16章 你过河 我拆桥\n在赵楷准备离开东京汴梁的时候，这座大宋的帝王之城，也开始全力备战了。\n" * 20
TRADITIONAL = "第16章 你過河 我拆橋\n在趙楷準備離開東京汴梁的時候，這座大宋的帝王之城，也開始全力備戰了。\n" * 20


def test_utf8_auto():
    r = decode_source(SIMPLIFIED.encode("utf-8"))
    assert r.text == SIMPLIFIED and r.encoding == "utf-8" and r.replacement_ratio == 0.0


def test_utf8_bom_is_stripped():
    r = decode_source(b"\xef\xbb\xbf" + "第1章 大王疯了\n".encode("utf-8"))
    assert r.text == "第1章 大王疯了\n"


def test_crlf_normalized_to_lf():
    r = decode_source("第一行\r\n第二行\r\n".encode("utf-8"))
    assert r.text == "第一行\n第二行\n"


def test_gbk_auto_detected():
    r = decode_source(SIMPLIFIED.encode("gbk"))
    assert r.text == SIMPLIFIED
    assert r.encoding in {"gb18030", "gbk", "gb2312"}


def test_big5_auto_detected():
    r = decode_source(TRADITIONAL.encode("big5"))
    assert r.text == TRADITIONAL
    assert r.encoding == "big5"


def test_explicit_gbk():
    assert decode_source(SIMPLIFIED.encode("gbk"), encoding="gbk").text == SIMPLIFIED


def test_forcing_wrong_encoding_raises_when_too_many_replacements():
    with pytest.raises(SourceDecodeError) as e:
        decode_source(SIMPLIFIED.encode("gbk"), encoding="utf-8")
    assert e.value.encoding == "utf-8" and e.value.ratio > 0.01


def test_empty_bytes():
    r = decode_source(b"")
    assert r.text == "" and r.encoding == "utf-8"


def test_unknown_encoding_raises_value_error_subclass():
    from app.core.textio import UnsupportedEncodingError

    with pytest.raises(UnsupportedEncodingError):
        decode_source(b"abc", encoding="latin-9")


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_utf16_with_bom_is_decoded(codec):
    bom = b"\xff\xfe" if codec.endswith("le") else b"\xfe\xff"
    r = decode_source(bom + SIMPLIFIED.encode(codec))
    assert r.text == SIMPLIFIED and r.encoding == "utf-16"
