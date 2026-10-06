"""Client DeepSeek (giao thức kiểu OpenAI, POST /chat/completions).

Mọi request PHẢI dựng bằng build_request (BR-8.6: luôn tắt thinking). Key chỉ nằm trong header, không bao giờ vào
body, message lỗi hay log."""
import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import astuple, dataclass, replace

import httpx

from app.config import get_settings

MAX_ATTEMPTS = 3  # BR-8.10
BACKOFF = (2.0, 4.0, 8.0)  # chờ sau lượt hỏng thứ 1, 2 (8s dành cho khi tăng MAX_ATTEMPTS)
RATE_LIMIT_WAIT = 60.0  # BR-8.12: 429 không có Retry-After
MAX_RATE_WAITS = 5
TIMEOUT_SECONDS = 300.0
THINKING_OFF = {"type": "disabled"}


class DeepSeekError(Exception):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class DeepSeekAuthError(DeepSeekError):
    """401 / 402 hoặc chưa có key: dừng cả pool (BR-8.13). status None nghĩa là chưa có key."""


class DeepSeekModelUnavailable(DeepSeekError):
    """404 / model không tồn tại: không thử lại (BR-8.11)."""


class DeepSeekTruncated(DeepSeekError):
    """finish_reason = length: output bị cắt ở max_tokens. Gửi lại cùng body chỉ tốn thêm tiền nên không thử lại."""

    def __init__(self, message: str):
        super().__init__(message, retryable=False)
        self.usage = Usage()  # token đã tốn (kể cả lượt này)


class DeepSeekRateLimited(DeepSeekError):
    """Đã chờ 429 đủ 5 lần (BR-8.12)."""


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    reasoning_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(*(a + b for a, b in zip(astuple(self), astuple(other))))

    @classmethod
    def from_json(cls, data: dict | None) -> "Usage":
        data = data or {}
        details = data.get("completion_tokens_details") or {}
        prompt = int(data.get("prompt_tokens") or 0)
        hit = int(data.get("prompt_cache_hit_tokens") or 0)
        miss = data.get("prompt_cache_miss_tokens")
        return cls(prompt, int(data.get("completion_tokens") or 0), hit,
                   int(miss) if miss is not None else max(0, prompt - hit), int(details.get("reasoning_tokens") or 0))


class OutputRejected(Exception):
    """validate() từ chối output (G1, G2 ≥ 5%, JSON hỏng): tính là một lượt thử."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.usage = Usage()  # token đã tốn cho các lượt bị từ chối


@dataclass(frozen=True)
class Completion:
    content: str
    usage: Usage  # cộng cả các lượt bị validate từ chối (đã tốn tiền)
    latency_ms: int  # lượt cuối, từ lúc gửi tới byte cuối (BR-5.1)
    attempts: int
    rate_waits: int
    finish_reason: str | None = None
    final_usage: Usage | None = None  # chỉ lượt cuối (usage ở trên cộng cả các lượt bị từ chối)


@dataclass
class RateBudget:
    """Số lần chờ 429 đã dùng. Truyền cùng một budget cho mọi request của một chương."""

    waits: int = 0


RetryHook = Callable[[int, str], Awaitable[None]]
UsageHook = Callable[["Completion"], Awaitable[None]]  # gọi cho MỌI lượt đã nhận usage, kể cả lượt sau đó bị từ chối


def build_request(model: str, messages: list[dict], *, temperature: float, max_tokens: int,
                  json_mode: bool = False) -> dict:
    """Hàm DUY NHẤT dựng body request DeepSeek (BR-8.6)."""
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": int(max_tokens),
            "stream": False, "thinking": dict(THINKING_OFF)}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    return body


def _message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        msg = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
    except ValueError:
        msg = None
    return str(msg or resp.text or resp.reason_phrase)[:300]


def _retry_after(resp: httpx.Response) -> float:
    try:
        return min(600.0, max(1.0, float(resp.headers.get("retry-after", ""))))
    except ValueError:
        return RATE_LIMIT_WAIT


class DeepSeekClient:
    def __init__(self, *, api_key: str, base_url: str, transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, timeout: float = TIMEOUT_SECONDS):
        self._key = (api_key or "").strip()
        self._base = base_url.rstrip("/")
        self._transport = transport
        self._sleep = sleep
        self._timeout = timeout

    @classmethod
    def from_settings(cls) -> "DeepSeekClient":
        s = get_settings()
        return cls(api_key=s.deepseek_api_key, base_url=s.deepseek_base_url)

    def _error_for(self, resp: httpx.Response) -> DeepSeekError | None:
        status = resp.status_code
        if status < 400:
            return None
        msg = _message(resp)
        if self._key:
            msg = msg.replace(self._key, "***")
        if status in (401, 402):
            return DeepSeekAuthError(f"DeepSeek từ chối key / hết số dư (HTTP {status}): {msg}", status=status)
        lowered = msg.lower()
        if status == 404 or (status == 400 and "model" in lowered
                             and any(w in lowered for w in ("not exist", "unavailable", "not found"))):
            return DeepSeekModelUnavailable(f"Model không dùng được (HTTP {status}): {msg}", status=status)
        return DeepSeekError(f"HTTP {status}: {msg}", status=status, retryable=status >= 500 or status == 408)

    def _parse(self, resp: httpx.Response, t0: float, attempt: int, waits: int) -> Completion:
        try:
            data = resp.json()
            choice = data["choices"][0]
            content = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
            raise DeepSeekError("DeepSeek trả JSON không đúng dạng", retryable=True) from e
        return Completion(content, Usage.from_json(data.get("usage")), int((time.perf_counter() - t0) * 1000),
                          attempt, waits, choice.get("finish_reason"))

    async def chat(self, body: dict, *, validate: Callable[[Completion], None] | None = None,
                   on_retry: RetryHook | None = None, budget: RateBudget | None = None,
                   on_usage: UsageHook | None = None, timeout: float | None = None,
                   max_rate_wait: float | None = None) -> Completion:
        if body.get("thinking") != THINKING_OFF:
            raise ValueError("Request DeepSeek phải dựng bằng build_request (BR-8.6)")
        if not self._key:
            raise DeepSeekAuthError("Chưa có DEEPSEEK_API_KEY trong file .env")
        budget = budget if budget is not None else RateBudget()
        spent = Usage()
        attempt = 0
        async with httpx.AsyncClient(base_url=self._base, transport=self._transport,
                                     timeout=self._timeout if timeout is None else timeout) as http:
            while True:
                attempt += 1
                t0 = time.perf_counter()
                failure: Exception
                try:
                    resp = await http.post("/chat/completions", json=body,
                                           headers={"Authorization": f"Bearer {self._key}"})
                except httpx.TransportError as e:
                    failure = DeepSeekError(f"Lỗi mạng khi gọi DeepSeek: {type(e).__name__}", retryable=True)
                else:
                    if resp.status_code == 429:
                        if budget.waits >= MAX_RATE_WAITS:
                            raise DeepSeekRateLimited("DeepSeek báo quá tải (HTTP 429) quá 5 lần", status=429)
                        budget.waits += 1
                        wait = _retry_after(resp)
                        if max_rate_wait is not None and wait > max_rate_wait:
                            raise DeepSeekRateLimited(f"DeepSeek báo quá tải (HTTP 429), yêu cầu chờ {wait:.0f}s", status=429)
                        if on_retry is not None:
                            await on_retry(attempt, f"HTTP 429, chờ {wait:.0f}s")
                        await self._sleep(wait)
                        attempt -= 1  # BR-8.12: lượt chờ 429 không tính
                        continue
                    err = self._error_for(resp)
                    if err is not None and not err.retryable:
                        raise err
                    if err is not None:
                        failure = err
                    else:
                        try:
                            completion = self._parse(resp, t0, attempt, budget.waits)
                        except DeepSeekError as e:
                            failure = e
                        else:
                            if on_usage is not None:
                                await on_usage(completion)
                            spent = spent + completion.usage
                            completion = replace(completion, usage=spent, final_usage=completion.usage)
                            if completion.finish_reason == "length":
                                truncated = DeepSeekTruncated(
                                    f"DeepSeek cắt cụt output vì chạm max_tokens={body.get('max_tokens')} "
                                    "(finish_reason=length), không gửi lại cùng request")
                                truncated.usage = spent
                                raise truncated
                            try:
                                if validate is not None:
                                    validate(completion)
                                return completion
                            except OutputRejected as rejected:
                                rejected.usage = spent
                                failure = rejected
                if attempt >= MAX_ATTEMPTS:
                    raise failure
                if on_retry is not None:
                    await on_retry(attempt, str(failure))
                await self._sleep(BACKOFF[attempt - 1])
