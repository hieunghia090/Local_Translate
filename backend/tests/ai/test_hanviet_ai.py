import pytest

from app.deepseek import hanviet_ai
from app.deepseek.client import OutputRejected
from fake_deepseek import FakeDeepSeek, reply, user_of


def test_parse_readings_keeps_only_asked_and_valid():
    # Review Focus 3
    content = ('{"赵": ["Triệu", "triệu khải", "x1"], "苏": "tô", "雪": [], "外": ["ngoại"], '
               '"藏": ["tàng", "tạng", "TÀNG"], "和": ["hòa", "hoà"], "楷": 5}')
    got = hanviet_ai.parse_readings(content, "赵苏雪藏和楷")
    assert got == {"赵": ["triệu"], "苏": ["tô"], "雪": [], "藏": ["tàng", "tạng"], "和": ["hòa"]}


def test_parse_readings_rejects_non_object():
    with pytest.raises(OutputRejected):
        hanviet_ai.parse_readings("không phải json", "赵")
    with pytest.raises(OutputRejected):
        hanviet_ai.parse_readings('["triệu"]', "赵")


def test_batches_of_400():
    assert [len(b) for b in hanviet_ai.batches([str(i) for i in range(950)])] == [400, 400, 150]


async def test_ask_readings_flash_json_thinking_off():
    fake = FakeDeepSeek()
    fake.script(reply('{"赵": ["triệu"], "苏": ["tô"]}', prompt=50, completion=20))
    found, completion = await hanviet_ai.ask_readings(fake.client(), ["赵", "苏"])
    assert found == {"赵": ["triệu"], "苏": ["tô"]} and completion.usage.prompt_tokens == 50
    body = fake.requests[0]
    assert body["model"] == "deepseek-flash" and body["thinking"] == {"type": "disabled"}
    assert body["response_format"] == {"type": "json_object"}
    assert user_of(body) == "# CHỮ CẦN TRA\n赵 苏"
