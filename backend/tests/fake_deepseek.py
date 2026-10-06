"""DeepSeek giả cho test: httpx.MockTransport, ghi lại mọi request, không gọi mạng."""
import asyncio
import inspect
import json
import re
from collections import deque
from collections.abc import Callable

import httpx

from app.deepseek.client import DeepSeekClient
from app.deepseek.estimate import text_tokens

FAKE_KEY = "sk-test-secret-key-123456"
_LINE = re.compile(r"^⟦(\d+)⟧ (.*)$")
_TEXT_HEADER = "# VĂN BẢN CẦN DỊCH"


def reply(content: str, *, prompt: int = 100, completion: int = 50, cached: int = 0, reasoning: int = 0,
          finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={
        "id": "fake", "object": "chat.completion", "model": "fake",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
                  "prompt_cache_hit_tokens": cached, "prompt_cache_miss_tokens": prompt - cached,
                  "completion_tokens_details": {"reasoning_tokens": reasoning}},
    })


def error(status: int, message: str = "lỗi giả", headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message, "type": "fake"}}, headers=headers)


def system_of(body: dict) -> str:
    return next((m["content"] for m in body["messages"] if m["role"] == "system"), "")


def user_of(body: dict) -> str:
    return next(m["content"] for m in body["messages"] if m["role"] == "user")


def sent_lines(body: dict) -> list[tuple[int, str]]:
    """Các dòng ⟦idx⟧ trong phần VĂN BẢN CẦN DỊCH của user prompt."""
    user = user_of(body)
    text = user.split(_TEXT_HEADER, 1)[1] if _TEXT_HEADER in user else ""
    out = []
    for line in text.split("\n"):
        m = _LINE.match(line)
        if m:
            out.append((int(m.group(1)), m.group(2)))
    return out


class FakeDeepSeek:
    def __init__(self, responder: Callable | None = None, *, delay: float = 0.0):
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        self.sleeps: list[float] = []
        self.scripted: deque = deque()
        self.delay = delay
        self.inflight = 0
        self.max_inflight = 0
        self._systems: set[str] = set()
        self.responder = responder or self.translate_lines()

    def script(self, *items) -> None:
        """Trả lần lượt các phản hồi này trước responder (httpx.Response hoặc hàm body -> Response)."""
        self.scripted.extend(items)

    def translate_lines(self, fn: Callable[[int, str], str | None] | None = None, *, ratio: float | None = None,
                        text_ratio: float | None = None) -> Callable:
        """Mỗi dòng ⟦i⟧ src thành ⟦i⟧ fn(i, src) (mặc định 'Câu i đã dịch.'); fn trả None = bỏ sót dòng đó.
        Usage: prompt ≈ số ký tự / 2; phần system trúng cache khi đã gặp system này; ratio đặt completion = prompt × ratio;
        text_ratio đặt completion = token văn bản nguồn của request × text_ratio (G5 so với token văn bản)."""
        fn = fn or (lambda i, _src: f"Câu {i} đã dịch.")

        def respond(body: dict) -> httpx.Response:
            system = system_of(body)
            content = "\n".join(f"⟦{i}⟧ {out}" for i, src in sent_lines(body) if (out := fn(i, src)) is not None)
            prompt = max(1, (len(system) + len(user_of(body))) // 2)
            if text_ratio is not None:
                completion = int(text_tokens("\n".join(src for _, src in sent_lines(body))) * text_ratio)
            else:
                completion = int(prompt * ratio) if ratio is not None else max(1, len(content) // 3)
            cached = min(prompt, len(system) // 2) if system in self._systems else 0
            self._systems.add(system)
            return reply(content, prompt=prompt, completion=completion, cached=cached)

        return respond

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(dict(request.headers))
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            item = self.scripted.popleft() if self.scripted else self.responder
            result = item(body) if callable(item) else item
            return await result if inspect.isawaitable(result) else result
        finally:
            self.inflight -= 1

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def client(self) -> DeepSeekClient:
        return DeepSeekClient(api_key=FAKE_KEY, base_url="https://fake.deepseek.test",
                              transport=httpx.MockTransport(self._handle), sleep=self._sleep)

    def user_prompts(self) -> list[str]:
        return [user_of(b) for b in self.requests]
