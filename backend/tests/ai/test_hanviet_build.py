import json
import zipfile

import httpx

from ai.g9_support import asked_chars
from app import hanviet_build as hb
from app.services.hanviet import parse_seed
from fake_deepseek import FakeDeepSeek, reply

OTHER = "# Unihan_OtherMappings.txt\nU+8D75\tkTGH\t2013:1378\nU+82CF\tkTGH\t2013:677\nU+96EA\tkTGH\t2013:2249\n" \
        "U+85CF\tkTGH\t2013:3392\nU+95E8\tkTGH\t2013:316\nU+8D75\tkGB0\t5431\n"
VARIANTS = "U+8D75\tkTraditionalVariant\tU+8D99\nU+82CF\tkTraditionalVariant\tU+56CC U+82CF U+8607\n" \
           "U+95E8\tkTraditionalVariant\tU+9580\n"
READINGS = "# comment\nU+8607\tkVietnamese\tto\nU+9580\tkVietnamese\tmôn\nU+85CF\tkVietnamese\ttàng\n" \
           "U+8D75\tkMandarin\tzhào\n"
AI = {"赵": ["triệu"], "趙": ["triệu"], "苏": ["tô"], "蘇": ["tô"], "囌": ["tô"], "雪": ["tuyết"],
      "藏": ["tàng", "tạng"], "门": ["môn"], "門": ["môn"]}
PRICES = {"price_in_per_mtok": 0.07, "price_in_cached_per_mtok": 0.02, "price_out_per_mtok": 0.28}


def unihan_zip(tmp_path):
    path = tmp_path / "Unihan.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Unihan_OtherMappings.txt", OTHER)
        z.writestr("Unihan_Variants.txt", VARIANTS)
        z.writestr("Unihan_Readings.txt", READINGS)
    return path


def fake_ai() -> FakeDeepSeek:
    return FakeDeepSeek(lambda body: reply(json.dumps({c: AI.get(c, []) for c in asked_chars(body)}, ensure_ascii=False),
                                           prompt=300, completion=150))


def test_parse_unihan_and_char_list(tmp_path):
    u = hb.read_unihan_zip(unihan_zip(tmp_path))
    assert u.tgh == ["赵", "苏", "雪", "藏", "门"]
    assert u.traditional["苏"] == ["囌", "蘇"]  # bỏ chính nó
    assert u.vietnamese["蘇"] == ["to"] and "赵" not in u.vietnamese
    assert hb.char_list(u) == ["赵", "苏", "雪", "藏", "门", "趙", "囌", "蘇", "門"]
    assert hb.unihan_readings(u, "苏") == ["to"] and hb.unihan_readings(u, "门") == ["môn"]


async def test_ac_4_16_build_seed_with_unihan_cross_check(tmp_path):
    fake = fake_ai()
    out = tmp_path / "data" / "hanviet_seed.tsv"
    result = await hb.build(fake.client(), hb.read_unihan_zip(unihan_zip(tmp_path)), out, prices=PRICES,
                            progress_path=tmp_path / "progress.json", echo=lambda *_: None)
    rows = {(c, r): s for c, r, s in parse_seed(out.read_text("utf-8"))}
    assert rows[("赵", "triệu")] == "ai" and rows[("苏", "tô")] == "ai" and rows[("雪", "tuyết")] == "ai"
    assert rows[("藏", "tàng")] == "confirmed" and rows[("藏", "tạng")] == "ai"
    assert rows[("门", "môn")] == "confirmed" and rows[("門", "môn")] == "confirmed"
    assert ("苏", "to") not in rows and ("蘇", "to") not in rows  # âm chỉ có ở Unihan thì không thêm (âm Nôm)
    assert out.read_text("utf-8").startswith("char\treading\tsource\tconfidence\n")
    assert len(fake.requests) == 1 and fake.requests[0]["model"] == "deepseek-flash"
    assert fake.requests[0]["thinking"] == {"type": "disabled"}
    assert (result.chars, result.confirmed, result.usage.prompt_tokens) == (9, 3, 300) and result.cost_usd > 0
    assert result.empty == []


async def test_progress_file_avoids_paying_twice_and_redo(tmp_path):
    u = hb.read_unihan_zip(unihan_zip(tmp_path))
    progress = tmp_path / "progress.json"
    fake = fake_ai()
    await hb.build(fake.client(), u, tmp_path / "a.tsv", prices=PRICES, progress_path=progress, echo=lambda *_: None)
    await hb.build(fake.client(), u, tmp_path / "b.tsv", prices=PRICES, progress_path=progress, echo=lambda *_: None)
    assert len(fake.requests) == 1
    await hb.build(fake.client(), u, tmp_path / "c.tsv", prices=PRICES, progress_path=progress, redo="苏藏",
                   echo=lambda *_: None)
    assert len(fake.requests) == 2 and sorted(asked_chars(fake.requests[1])) == sorted("苏藏")
    assert (tmp_path / "a.tsv").read_text("utf-8") == (tmp_path / "c.tsv").read_text("utf-8")


def test_download_unihan_once(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=b"PK-zip-bytes")

    dest = tmp_path / "cache" / "Unihan.zip"
    assert hb.download_unihan(dest, transport=httpx.MockTransport(handler)) == dest
    hb.download_unihan(dest, transport=httpx.MockTransport(handler))
    assert calls == [hb.UNIHAN_URL] and dest.read_bytes() == b"PK-zip-bytes"
    assert not dest.with_suffix(".part").exists()
