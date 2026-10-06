"""Sinh âm Hán Việt bằng DeepSeek (spec 04 mục 6a, bước 1 và 3). Dùng chung cho `make hanviet` và bổ sung nền."""
import json
import re
from collections.abc import Iterable

from app.core.hanviet import normalize_reading, syllable_key
from app.deepseek.client import Completion, DeepSeekClient, OutputRejected, build_request

MODEL = "deepseek-flash"  # BR-4.19
BATCH_SIZE = 400
TEMPERATURE = 0.1
TOKENS_PER_CHAR_OUT = 16
MAX_TOKENS = 8192
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")

HANVIET_SYSTEM = """Bạn là chuyên gia âm Hán Việt.
Với mỗi chữ Hán trong mục "CHỮ CẦN TRA", liệt kê các âm Hán Việt chuẩn của chữ đó, như trong từ điển Hán Việt.
- Chỉ âm Hán Việt. Không ghi âm Nôm, không ghi nghĩa, không ghi pinyin.
- Mỗi âm là một âm tiết, viết thường, có dấu (ví dụ "triệu", "tô", "tàng").
- Chữ có nhiều âm thì liệt kê đủ, âm thông dụng nhất đứng đầu.
- Chữ không có âm Hán Việt thì trả mảng rỗng.
Trả về đúng một đối tượng JSON, khoá là chữ Hán, giá trị là mảng âm:
{"赵": ["triệu"], "藏": ["tàng", "tạng"]}"""


def hanviet_user_prompt(chars: Iterable[str]) -> str:
    return "# CHỮ CẦN TRA\n" + " ".join(chars)


def batches(chars: list[str], size: int = BATCH_SIZE) -> list[list[str]]:
    return [chars[i:i + size] for i in range(0, len(chars), size)]


def parse_readings(content: str, asked: Iterable[str]) -> dict[str, list[str]]:
    """Chỉ giữ chữ đã hỏi; mỗi âm phải là một âm tiết hợp lệ; bỏ âm trùng (hoà = hòa). JSON hỏng thì OutputRejected."""
    try:
        data = json.loads(_FENCE.sub("", content.strip()))
    except ValueError as e:
        raise OutputRejected("json", "JSON âm Hán Việt không hợp lệ") from e
    if not isinstance(data, dict):
        raise OutputRejected("json", "JSON âm Hán Việt phải là một đối tượng")
    wanted = set(asked)
    out: dict[str, list[str]] = {}
    for ch, values in data.items():
        if ch not in wanted:
            continue
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            continue
        readings: list[str] = []
        keys: set[str] = set()
        for v in values:
            r = normalize_reading(v)
            if r and syllable_key(r) not in keys:
                keys.add(syllable_key(r))
                readings.append(r)
        out[ch] = readings
    return out


async def ask_readings(client: DeepSeekClient, chars: list[str], *, model: str = MODEL) -> tuple[dict[str, list[str]], Completion]:
    body = build_request(
        model, [{"role": "system", "content": HANVIET_SYSTEM}, {"role": "user", "content": hanviet_user_prompt(chars)}],
        temperature=TEMPERATURE, max_tokens=min(MAX_TOKENS, max(1024, len(chars) * TOKENS_PER_CHAR_OUT)), json_mode=True)
    holder: dict = {}

    def validate(c: Completion) -> None:
        holder["readings"] = parse_readings(c.content, chars)

    completion = await client.chat(body, validate=validate)
    return holder["readings"], completion
