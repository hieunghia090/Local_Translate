import httpx
import pytest

from app.deepseek.client import (
    DeepSeekAuthError, DeepSeekClient, DeepSeekError, DeepSeekModelUnavailable, DeepSeekRateLimited, DeepSeekTruncated,
    OutputRejected,
    RateBudget, build_request,
)
from fake_deepseek import FAKE_KEY, FakeDeepSeek, error, reply

MSG = [{"role": "user", "content": "xin chào"}]


def body(**kw) -> dict:
    return build_request("deepseek-flash", MSG, temperature=0.3, max_tokens=1024, **kw)


def test_build_request_always_disables_thinking():
    # BR-8.6, AC-8.16
    b = build_request("deepseek-v4-pro", MSG, temperature=0.3, max_tokens=2000)
    assert b["thinking"] == {"type": "disabled"} and b["max_tokens"] == 2000 and "response_format" not in b
    j = build_request("deepseek-flash", MSG, temperature=0.1, max_tokens=1024, json_mode=True)
    assert j["thinking"] == {"type": "disabled"} and j["response_format"] == {"type": "json_object"}


async def test_chat_returns_content_and_usage():
    fake = FakeDeepSeek()
    fake.script(reply("Xin chào", prompt=120, completion=30, cached=100, reasoning=0))
    c = await fake.client().chat(body())
    assert c.content == "Xin chào" and c.attempts == 1 and c.rate_waits == 0
    u = c.usage
    assert (u.prompt_tokens, u.completion_tokens, u.prompt_cache_hit_tokens, u.prompt_cache_miss_tokens, u.reasoning_tokens) == (120, 30, 100, 20, 0)
    assert fake.requests[0]["thinking"] == {"type": "disabled"}
    assert fake.headers[0]["authorization"] == f"Bearer {FAKE_KEY}"


async def test_refuses_body_built_without_build_request():
    with pytest.raises(ValueError):
        await FakeDeepSeek().client().chat({"model": "deepseek-flash", "messages": MSG})


async def test_server_errors_retry_with_backoff():
    fake = FakeDeepSeek()
    fake.script(error(500), error(503), reply("ok"))
    c = await fake.client().chat(body())
    assert c.content == "ok" and c.attempts == 3 and fake.sleeps == [2.0, 4.0]


async def test_gives_up_after_three_attempts():
    # BR-8.10
    fake = FakeDeepSeek()
    fake.script(error(500), error(500), error(500), reply("không tới"))
    with pytest.raises(DeepSeekError) as e:
        await fake.client().chat(body())
    assert e.value.status == 500 and len(fake.requests) == 3


async def test_404_stops_immediately():
    # BR-8.11
    fake = FakeDeepSeek()
    fake.script(error(404, "Model Not Exist"))
    with pytest.raises(DeepSeekModelUnavailable):
        await fake.client().chat(body())
    assert len(fake.requests) == 1 and fake.sleeps == []


async def test_model_not_exist_400_is_unavailable():
    fake = FakeDeepSeek()
    fake.script(error(400, "Model Not Exist"))
    with pytest.raises(DeepSeekModelUnavailable):
        await fake.client().chat(body())


@pytest.mark.parametrize("status", [401, 402])
async def test_auth_errors_never_retry_and_hide_key(status):
    # BR-8.13, AC-5.3
    fake = FakeDeepSeek()
    fake.script(error(status, f"bad key {FAKE_KEY}"))
    with pytest.raises(DeepSeekAuthError) as e:
        await fake.client().chat(body())
    assert e.value.status == status and FAKE_KEY not in str(e.value) and len(fake.requests) == 1


async def test_429_waits_retry_after_without_using_attempts():
    # BR-8.12
    fake = FakeDeepSeek()
    fake.script(error(429, headers={"Retry-After": "7"}), error(429), error(500), error(500), reply("ok"))
    c = await fake.client().chat(body())
    assert c.content == "ok" and c.attempts == 3 and c.rate_waits == 2
    assert fake.sleeps == [7.0, 60.0, 2.0, 4.0]


async def test_429_more_than_five_times_gives_up():
    fake = FakeDeepSeek()
    fake.script(*[error(429, headers={"Retry-After": "1"}) for _ in range(6)])
    with pytest.raises(DeepSeekRateLimited):
        await fake.client().chat(body())
    assert len(fake.requests) == 6 and fake.sleeps == [1.0] * 5


async def test_rate_budget_shared_across_calls_of_one_chapter():
    fake = FakeDeepSeek()
    fake.script(*[error(429, headers={"Retry-After": "1"}) for _ in range(3)], reply("a"),
                *[error(429, headers={"Retry-After": "1"}) for _ in range(3)])
    client, budget = fake.client(), RateBudget()
    assert (await client.chat(body(), budget=budget)).content == "a"
    with pytest.raises(DeepSeekRateLimited):
        await client.chat(body(), budget=budget)
    assert budget.waits == 5


async def test_rejected_output_counts_as_attempt_and_usage_adds_up():
    fake = FakeDeepSeek()
    fake.script(reply("xấu", prompt=10, completion=5), reply("tốt", prompt=10, completion=5))
    seen = []

    def validate(c):
        if c.content == "xấu":
            raise OutputRejected("han_output", "Bản dịch vẫn còn là tiếng Trung")

    async def on_retry(n, reason):
        seen.append((n, reason))

    c = await fake.client().chat(body(), validate=validate, on_retry=on_retry)
    assert c.content == "tốt" and c.attempts == 2 and c.usage.prompt_tokens == 20 and c.usage.completion_tokens == 10
    assert seen == [(1, "Bản dịch vẫn còn là tiếng Trung")] and fake.sleeps == [2.0]


async def test_rejected_three_times_raises_with_usage():
    fake = FakeDeepSeek()
    fake.script(*[reply("xấu", prompt=10, completion=5) for _ in range(3)])

    def validate(c):
        raise OutputRejected("han_output", "Bản dịch vẫn còn là tiếng Trung")

    with pytest.raises(OutputRejected) as e:
        await fake.client().chat(body(), validate=validate)
    assert e.value.usage.prompt_tokens == 30 and len(fake.requests) == 3


async def test_network_error_is_retried():
    calls = {"n": 0}

    def responder(_body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("mất mạng")
        return reply("ok")

    fake = FakeDeepSeek(responder)
    assert (await fake.client().chat(body())).content == "ok" and fake.sleeps == [2.0]


async def test_missing_key_is_auth_error_without_request():
    with pytest.raises(DeepSeekAuthError) as e:
        await DeepSeekClient(api_key="  ", base_url="https://x.test").chat(body())
    assert e.value.status is None


async def test_finish_reason_length_is_not_retried_nor_resent():
    # G5: output bị cắt cụt thì gửi lại cùng body cũng cắt cụt: lỗi ngay, không lặp, vẫn báo usage đã tốn
    fake = FakeDeepSeek()
    fake.script(reply("⟦1⟧ dở dang", prompt=500, completion=1024, finish_reason="length"), reply("không tới"))
    with pytest.raises(DeepSeekTruncated) as e:
        await fake.client().chat(body())
    assert len(fake.requests) == 1 and fake.sleeps == []
    assert e.value.retryable is False and "max_tokens" in str(e.value) and "1024" in str(e.value)
    assert (e.value.usage.prompt_tokens, e.value.usage.completion_tokens) == (500, 1024)


async def test_per_call_timeout_overrides_client_timeout():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return reply("ok")

    client = DeepSeekClient(api_key=FAKE_KEY, base_url="https://x.test", transport=httpx.MockTransport(handler), timeout=300)
    await client.chat(body())
    await client.chat(body(), timeout=20)
    assert seen == [300, 20]
