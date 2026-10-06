from app.honorific.engine import SegInput, active_layers, apply_chapter, classify, preview


def test_classify_with_genre_prior():
    assert classify("朕与陛下在宗门修为", "other")[0] == "ancient"
    assert classify("手机电脑公司老板警察", "other")[0] == "modern"
    assert classify("坦克步枪部队首长", "xianxia")[0] == "modern"
    assert classify("官家陛下坦克步枪", "other")[0] == "mixed"
    assert classify("今天天气很好", "other") == ("unknown", 0.0)
    assert classify("今天天气很好", "xianxia")[0] == "ancient"
    assert classify("今天天气很好", "urban")[0] == "modern"


def test_active_layers():
    cfg = {"kinship": True, "pronoun": True, "modern_stable": True}
    assert active_layers("ancient", cfg) == {"kinship", "pronoun"}
    assert active_layers("mixed", cfg) == {"kinship"}
    assert active_layers("modern", cfg) == {"modern_stable"}
    assert active_layers("unknown", cfg) == set()


def test_offsets_after_several_edits_in_one_sentence():
    # Review Focus 2
    outs, _ = apply_chapter([SegInput("他看着她，他笑了。", "Anh ta nhìn cô ấy, anh ta cười.")], "ancient", {"pronoun": True})
    out = outs[0]
    assert out.dst == "Hắn nhìn nàng, hắn cười."
    assert [(e["from"], e["to"], e["offset"]) for e in out.edits] == [("Anh ta", "Hắn", 0), ("cô ấy", "nàng", 9), ("anh ta", "hắn", 15)]
    assert all(e["rule"].startswith("pronoun.") and e["src_token"] in ("他", "她") for e in out.edits)


def test_unclosed_quote_does_not_misalign():
    # Review Focus 5
    outs, stats = apply_chapter([SegInput("“你来了。他说", "“Anh tới rồi. Anh ta nói")], "ancient", {"pronoun": True})
    assert outs[0].dst == "“Anh tới rồi. Hắn nói"
    assert stats.applied == {"pronoun": 1}


def test_preview_shape():
    r = preview("师姐来了。", "Chị tới rồi.", "ancient", {"kinship": True})
    assert r["dst"] == "Sư tỷ tới rồi." and r["edits"][0]["rule"] == "kinship.师姐" and r["skipped"] == {}


def test_nfd_input_is_normalized_to_nfc():
    import unicodedata

    raw = unicodedata.normalize("NFD", "Sư anh, anh cũng tới rồi?")
    assert raw != unicodedata.normalize("NFC", raw)
    outs, stats = apply_chapter([SegInput("师兄，你也来了？", raw)], "ancient", {"kinship": True})
    assert outs[0].dst == "Sư huynh, anh cũng tới rồi?" and stats.skipped == {}
    assert preview("师姐来了。", unicodedata.normalize("NFD", "Chị tới rồi."), "ancient", {"kinship": True})["dst"] == "Sư tỷ tới rồi."
