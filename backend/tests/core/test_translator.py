import re

import pytest

from app.core.translator import BatchResult, FakeTranslator, Translator


def test_fake_translator_contract():
    t: Translator = FakeTranslator()
    r = t.translate(["你好。", "再见。"], beam=2, batch_size=8)
    assert isinstance(r, BatchResult)
    assert r.outputs == ["VI<你好。>", "VI<再见。>"]
    assert r.tokens_in == 6 and r.tokens_out == len("VI<你好。>") + len("VI<再见。>")
    assert r.truncated == [False, False]
    assert t.calls == [["你好。", "再见。"]]


def test_fake_translator_empty():
    assert FakeTranslator().translate([], beam=1, batch_size=8) == BatchResult([], 0, 0, [])


@pytest.mark.slow
def test_ct2_translates_real_sentence():
    from app.core.ct2_translator import CT2Translator

    t = CT2Translator(threads=4)
    r = t.translate(["林凡睁开双眼，发现自己躺在一间破旧的木屋里。"], beam=2, batch_size=8)
    assert len(r.outputs) == 1 and r.outputs[0].strip()
    assert not re.search(r"[一-鿿]", r.outputs[0])
    assert r.tokens_in > 0 and r.tokens_out > 0
    assert r.truncated == [False]
    assert t.model_id == "HachimiMT-60"


@pytest.mark.slow
def test_ct2_truncation_matches_tokenizer_truncation():
    from app.core.ct2_translator import MAX_SRC_TOKENS, CT2Translator

    t = CT2Translator(threads=4)
    long_text = "赵楷准备离开东京汴梁，" * 80
    expected = t._tok.encode(long_text, truncation=True, max_length=MAX_SRC_TOKENS)
    ids, truncated = t._encode(long_text)
    assert ids == expected and truncated is True
    short = "你好。"
    assert t._encode(short) == (t._tok.encode(short), False)
