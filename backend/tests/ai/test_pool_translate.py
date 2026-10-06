import asyncio

import pytest
from sqlalchemy import select, update

from app.core.translator import FakeTranslator
from app.db import get_sessionmaker
from app.models import Chapter, ChapterNote, ChapterRevision, Job, JobSegment, utcnow
from app.services import queue
from app.worker import Worker
from ai.support import summaries, ds_book, get, log_rows, pool_for, segments
from fake_deepseek import FakeDeepSeek, sent_lines, system_of
from helpers import enqueue

pytestmark = pytest.mark.db


async def test_translates_chapter_keeping_meta_lines(api):
    # AC-8.1, BR-8.2, BR-8.5, BR-8.9
    book_id, chapters = await ds_book(api, lines=3)
    (job_id,) = await enqueue(book_id)
    fake = FakeDeepSeek()
    await pool_for(fake).run_until_idle()

    ch = await get(Chapter, chapters[0].id)
    assert (ch.status, ch.model_id, ch.error) == ("translated", "deepseek-v4-pro", None)
    rows = await segments(ch.id)
    for s in rows:
        if s.is_meta:
            assert s.dst == s.src
        else:
            assert s.dst == f"Câu {s.idx} đã dịch." and s.dst_model_raw == s.dst_machine == s.dst
            assert s.honorific_edits == [] and s.flags == []
    assert [i for i, _ in sent_lines(fake.requests[0])] == [s.idx for s in rows if not s.is_meta]
    assert all("=====" not in src for _, src in sent_lines(fake.requests[0]))
    job = await get(Job, job_id)
    assert (job.status, job.engine, job.progress) == ("done", "deepseek", 100) and job.tokens_in > 0
    async with get_sessionmaker()() as s:
        rev = (await s.scalars(select(ChapterRevision))).one()
        assert (await s.scalars(select(JobSegment))).all() == []
    assert (rev.kind, rev.model_id) == ("machine", "deepseek-v4-pro")
    (line,) = await summaries(source="translate")
    assert (line.level, line.provider, line.model) == ("info", "deepseek", "deepseek-v4-pro")
    assert line.tokens_in > 0 and line.tokens_out > 0 and line.tokens_in_est > 0 and line.tokens_out_est > 0
    assert float(line.cost_usd) > 0 and line.latency_ms >= 0
    assert line.detail["request"]["thinking"] == {"type": "disabled"}
    assert line.detail["glossary"] == {"matched": 0, "sent": 0, "skipped_predictable": 0, "truncated": 0, "tokens_est": 0}
    assert "estimate" not in line.params and line.params["summary"] is True  # chỉ dòng request mang estimate
    (req,) = [r for r in await log_rows(source="translate") if r.params.get("partial")]
    assert req.params["estimate"]["text_tokens"] > 0 and req.tokens_in == line.tokens_in


async def test_title_from_marker_line_unless_user_edited(api):
    # BR-8.8
    book_id, chapters = await ds_book(api, n=2, lines=1)
    await api.patch(f"/api/v1/chapters/{chapters[1].id}", json={"title_vi": "Tên tôi đặt"})
    fake = FakeDeepSeek()
    fake.responder = fake.translate_lines(lambda i, src: "## Chương mới: Tiêu đề " if "标题" in src else "Thân.")
    await enqueue(book_id)
    await pool_for(fake).run_until_idle()
    assert (await get(Chapter, chapters[0].id)).title_vi == "Chương mới: Tiêu đề"
    assert (await get(Chapter, chapters[1].id)).title_vi == "Tên tôi đặt"


async def test_second_chapter_hits_cache_with_identical_system_and_gets_context(api):
    # AC-8.2, BR-8.1, BR-8.4
    book_id, chapters = await ds_book(api, n=2, lines=2)
    await enqueue(book_id)
    fake = FakeDeepSeek()
    await pool_for(fake).run_until_idle()
    assert len(fake.requests) == 2
    assert system_of(fake.requests[0]) == system_of(fake.requests[1])
    lines = sorted(await summaries(source="translate"), key=lambda x: x.chapter_no)
    assert lines[0].tokens_in_cached == 0 and lines[1].tokens_in_cached > 0
    first, second = fake.user_prompts()
    assert "# NGỮ CẢNH" not in first
    assert "# NGỮ CẢNH" in second and "Câu 4 đã dịch." in second.split("# NGỮ CẢNH")[1]


async def test_correction_note_sent_then_resolved(api):
    # AC-8.10
    book_id, chapters = await ds_book(api, lines=1)
    async with get_sessionmaker()() as s:
        s.add(ChapterNote(chapter_id=chapters[0].id, type="correction", content="Tên 高俅 phải là Cao Cầu",
                          resolved=False, created_at=utcnow()))
        s.add(ChapterNote(chapter_id=chapters[0].id, type="general", content="ghi chú riêng", resolved=False,
                          created_at=utcnow()))
        await s.commit()
    await enqueue(book_id)
    fake = FakeDeepSeek()
    await pool_for(fake).run_until_idle()
    user = fake.user_prompts()[0]
    assert "- [correction] Tên 高俅 phải là Cao Cầu" in user and "ghi chú riêng" not in user
    async with get_sessionmaker()() as s:
        notes = {n.type: n.resolved for n in (await s.scalars(select(ChapterNote))).all()}
    assert notes == {"correction": True, "general": False}


async def test_concurrency_cap_while_cpu_worker_runs(api):
    # AC-8.11, BR-8.15, NFR-4
    book_id, chapters = await ds_book(api, n=8, lines=1, concurrency=3)
    await enqueue(book_id, [chapters[0].id], engine="ct2")
    await enqueue(book_id, [c.id for c in chapters[1:]])
    fake = FakeDeepSeek(delay=0.3)
    cpu = Worker(FakeTranslator)

    async def cpu_loop():
        while await cpu.run_once():
            pass

    await asyncio.gather(pool_for(fake).run_until_idle(), cpu_loop())
    assert fake.max_inflight == 3 and len(fake.requests) == 7
    assert (await get(Chapter, chapters[0].id)).model_id == "fake"
    for c in chapters[1:]:
        assert (await get(Chapter, c.id)).model_id == "deepseek-v4-pro"


async def test_cancel_during_request_discards_result(api):
    # Review Focus 1
    book_id, chapters = await ds_book(api, lines=2)
    (job_id,) = await enqueue(book_id)
    fake = FakeDeepSeek()
    base = fake.responder

    async def cancelling(body):
        async with get_sessionmaker()() as s:
            await queue.cancel_job(s, job_id)
            await s.commit()
        return base(body)

    fake.responder = cancelling
    await pool_for(fake).run_until_idle()
    assert (await get(Job, job_id)).status == "cancelled"
    assert (await get(Chapter, chapters[0].id)).status == "todo"
    assert await segments(chapters[0].id) == []
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(JobSegment))).all() == []


async def test_pause_during_request_keeps_result_and_resumes_without_new_request(api):
    # Review Focus 1, BR-3.9
    book_id, chapters = await ds_book(api, lines=2)
    (job_id,) = await enqueue(book_id)
    fake = FakeDeepSeek()
    base = fake.responder

    async def pausing(body):
        async with get_sessionmaker()() as s:
            await queue.set_paused(s, "deepseek", True)
            await s.commit()
        return base(body)

    fake.responder = pausing
    pool = pool_for(fake)
    await pool.run_until_idle()
    assert (await get(Job, job_id)).status == "paused"
    assert (await get(Chapter, chapters[0].id)).status == "queued"
    async with get_sessionmaker()() as s:
        assert len((await s.scalars(select(JobSegment))).all()) == 3  # tiêu đề + 2 câu
        await queue.set_paused(s, "deepseek", False)
        await s.commit()
    fake.responder = base
    await pool.run_until_idle()
    assert len(fake.requests) == 1
    assert (await get(Chapter, chapters[0].id)).status == "translated"


async def test_restart_recovers_running_deepseek_jobs(api):
    # Review Focus 2, NFR-3
    book_id, chapters = await ds_book(api, n=2, lines=1)
    (t_job,) = await enqueue(book_id, [chapters[0].id])
    async with get_sessionmaker()() as s:
        await s.execute(update(Job).where(Job.id == t_job).values(status="running"))
        await s.execute(update(Chapter).where(Chapter.id == chapters[0].id).values(status="translating"))
        await s.execute(update(Chapter).where(Chapter.id == chapters[1].id).values(status="translated", model_id="HachimiMT-60"))
        s.add(Job(book_id=chapters[1].book_id, chapter_id=chapters[1].id, kind="review", engine="deepseek",
                  status="running", progress=0, position=99, run_config={}, options={}))
        await s.commit()
    await Worker(FakeTranslator).startup()  # worker CPU không đụng job DeepSeek
    assert (await get(Job, t_job)).status == "running"
    await pool_for(FakeDeepSeek()).startup()
    async with get_sessionmaker()() as s:
        kinds = {j.kind: j.status for j in (await s.scalars(select(Job))).all()}
    assert kinds == {"translate": "queued", "review": "queued"}
    assert (await get(Chapter, chapters[0].id)).status == "queued"
    assert (await get(Chapter, chapters[1].id)).status == "translated"


async def test_run_forever_stops_on_event(api):
    stop = asyncio.Event()
    task = asyncio.create_task(pool_for(FakeDeepSeek()).run_forever(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 5)
