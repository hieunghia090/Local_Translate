"""Fix 7: mọi request DeepSeek đã trả usage đều có một dòng log `partial` (cơ sở duy nhất của chi phí tháng và hiệu chỉnh);
dòng tổng của chương chỉ để hiển thị. Phần dịch dở được giữ khi lỗi có thể thử lại."""
import pytest
from sqlalchemy import func, select, update

from app.db import get_sessionmaker
from app.models import AiModel, Chapter, Job, JobSegment
from ai.support import ds_book, get, log_rows, pool_for, segments
from fake_deepseek import FakeDeepSeek, error, sent_lines
from helpers import enqueue

pytestmark = pytest.mark.db


async def _small_window():
    async with get_sessionmaker()() as s:
        await s.execute(update(AiModel).where(AiModel.id == "deepseek-v4-pro").values(context_window=600, max_output_tokens=256))
        await s.commit()


def _partial(rows):
    return [r for r in rows if (r.params or {}).get("partial") is True]


def _summary(rows):
    return [r for r in rows if (r.params or {}).get("partial") is not True]


async def _staged(job_id) -> int:
    async with get_sessionmaker()() as s:
        return await s.scalar(select(func.count()).select_from(JobSegment).where(JobSegment.job_id == job_id))


async def test_each_request_logs_a_partial_usage_row_and_usage_counts_only_those(api):
    book_id, chapters = await ds_book(api, lines=60)
    await _small_window()
    await enqueue(book_id)
    fake = FakeDeepSeek()
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) >= 2
    rows = await log_rows(provider="deepseek", source="translate")
    part, summary = _partial(rows), _summary(rows)
    assert len(part) == len(fake.requests) and len(summary) == 1
    assert all(r.tokens_in > 0 and r.cost_usd is not None and r.params["estimate"]["text_tokens"] > 0 for r in part)
    assert summary[0].tokens_in == sum(r.tokens_in for r in part)  # dòng tổng chỉ hiển thị
    assert float(summary[0].cost_usd) == pytest.approx(sum(float(r.cost_usd) for r in part), abs=1e-5)
    usage = (await api.get(f"/api/v1/books/{book_id}/usage")).json()
    (item,) = usage["items"]
    assert item["requests"] == len(part) and item["tokens_in"] == sum(r.tokens_in for r in part)
    assert usage["total"]["cost_usd"] == pytest.approx(sum(float(r.cost_usd) for r in part), abs=1e-5)


async def test_rejected_attempts_are_logged_as_requests(api):
    book_id, chapters = await ds_book(api, lines=2)
    await enqueue(book_id)
    fake = FakeDeepSeek()
    fake.responder = fake.translate_lines(lambda i, src: "这是中文这是中文这是中文")
    await pool_for(fake).run_until_idle()
    rows = await log_rows(provider="deepseek", source="translate")
    assert len(_partial(rows)) == 3 == len(fake.requests)


async def test_retryable_failure_keeps_staging_logs_spend_and_retry_does_not_pay_again(api):
    book_id, chapters = await ds_book(api, lines=60)
    await _small_window()
    (job_id,) = await enqueue(book_id)
    fake = FakeDeepSeek()
    fake.script(fake.translate_lines(), error(500), error(500), error(500))
    await pool_for(fake).run_until_idle()
    assert (await get(Job, job_id)).status == "failed" and (await get(Chapter, chapters[0].id)).status == "error"
    first_done = [i for i, _ in sent_lines(fake.requests[0])]
    assert await _staged(job_id) == len(first_done)  # giữ phần đã dịch
    rows = await log_rows(provider="deepseek", source="translate")
    assert len(_partial(rows)) == 1  # request thành công đầu tiên đã tốn tiền dù chương lỗi
    assert (await api.get(f"/api/v1/books/{book_id}/usage")).json()["total"]["requests"] == 1

    # thử lại: job mới nhận lại phần đã dịch, chỉ gửi các dòng còn lại
    (retry_id,) = await enqueue(book_id)
    fake2 = FakeDeepSeek()
    await pool_for(fake2).run_until_idle()
    resent = {i for b in fake2.requests for i, _ in sent_lines(b)}
    assert resent and not resent & set(first_done)
    assert (await get(Chapter, chapters[0].id)).status == "translated"
    assert all(s.dst.strip() for s in await segments(chapters[0].id) if s.src.strip())
    assert await _staged(job_id) == 0 and await _staged(retry_id) == 0
    total_rows = _partial(await log_rows(provider="deepseek", source="translate"))
    assert len(total_rows) == 1 + len(fake2.requests)
    usage = (await api.get(f"/api/v1/books/{book_id}/usage")).json()
    assert usage["total"]["tokens_in"] == sum(r.tokens_in for r in total_rows)


async def test_non_retryable_failure_deletes_staging(api):
    book_id, chapters = await ds_book(api, lines=60)
    await _small_window()
    (job_id,) = await enqueue(book_id)
    fake = FakeDeepSeek()
    fake.script(fake.translate_lines(), error(404, "Model Not Exist"))
    await pool_for(fake).run_until_idle()
    assert (await get(Job, job_id)).status == "failed"
    assert await _staged(job_id) == 0
    assert len(_partial(await log_rows(provider="deepseek"))) == 1  # chi phí request đầu vẫn được ghi


async def test_pause_keeps_partial_usage_and_resume_logs_the_rest(api):
    from app.services import queue

    book_id, chapters = await ds_book(api, lines=60)
    await _small_window()
    await enqueue(book_id)
    fake = FakeDeepSeek()

    async def pause_after_first(body):
        async with get_sessionmaker()() as s:
            await queue.set_paused(s, "deepseek", True)
            await s.commit()
        return fake.translate_lines()(body)

    fake.script(pause_after_first)
    await pool_for(fake).run_until_idle()
    assert len(_partial(await log_rows(provider="deepseek"))) == 1
    async with get_sessionmaker()() as s:
        await queue.set_paused(s, "deepseek", False)
        await s.commit()
    await pool_for(fake).run_until_idle()
    rows = await log_rows(provider="deepseek", source="translate")
    assert len(_partial(rows)) == len(fake.requests) and len(_summary(rows)) == 1
    assert _summary(rows)[0].tokens_in == sum(r.tokens_in for r in _partial(rows))  # tổng toàn job, kể cả lượt trước khi tạm dừng
