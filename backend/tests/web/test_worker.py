import asyncio
import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy import select, update

from app.core.translator import BatchResult, FakeTranslator
from app.db import get_sessionmaker
from app.models import Book, Chapter, ChapterRevision, Job, JobSegment, LogEntry, Segment
from app.services import queue
from app.worker import Worker
from helpers import enqueue, make_book

pytestmark = pytest.mark.db


async def _get(model, id_):
    async with get_sessionmaker()() as s:
        return await s.get(model, id_)


async def _segments(chapter_id):
    async with get_sessionmaker()() as s:
        return list((await s.scalars(select(Segment).where(Segment.chapter_id == chapter_id).order_by(Segment.idx))).all())


async def _logs(**where):
    async with get_sessionmaker()() as s:
        stmt = select(LogEntry).order_by(LogEntry.id)
        for k, v in where.items():
            stmt = stmt.where(getattr(LogEntry, k) == v)
        return list((await s.scalars(stmt)).all())


def sent(fake: FakeTranslator) -> list[str]:
    return [t for call in fake.calls for t in call]


async def test_worker_translates_chapter_end_to_end(api):
    # AC-3.4, AC-5.1
    book_id, chapters = await make_book(api, n_chapters=1, lines=4)
    (job_id,) = await enqueue(book_id)
    fake = FakeTranslator()
    worker = Worker(lambda: fake, lines_per_step=2)
    assert await worker.run_once() is True
    assert await worker.run_once() is False  # hết job

    ch = await _get(Chapter, chapters[0].id)
    assert (ch.status, ch.model_id, ch.error) == ("translated", "fake", None)
    assert ch.translated_at is not None
    segs = await _segments(ch.id)
    assert [s.is_meta for s in segs][:3] == [False, True, True]
    assert segs[0].dst == "VI<第1章 标题1>" and segs[0].dst_machine == segs[0].dst
    assert segs[1].dst == "========="  # dòng meta giữ nguyên
    job = await _get(Job, job_id)
    assert (job.status, job.progress) == ("done", 100) and job.tokens_in > 0 and job.tokens_out > 0
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(JobSegment))).all() == []
        rev = (await s.scalars(select(ChapterRevision))).one()
    assert (rev.kind, rev.model_id, rev.segments_changed) == ("machine", "fake", 5)
    translate_logs = await _logs(source="translate")
    assert len(translate_logs) == 1  # BR-5.2: một dòng mỗi chương
    entry = translate_logs[0]
    assert entry.level == "info" and entry.tokens_in > 0 and entry.tokens_out > 0 and entry.latency_ms >= 0
    assert entry.chapter_no == 1 and entry.params["beam"] == 2


async def test_jobs_run_in_queue_order(api):
    book_id, chapters = await make_book(api, n_chapters=3, lines=1)
    ids = await enqueue(book_id)
    async with get_sessionmaker()() as s:
        await queue.move_job(s, ids[2], 0)
        await s.commit()
    fake = FakeTranslator()
    worker = Worker(lambda: fake)
    while await worker.run_once():
        pass
    assert [call[0] for call in fake.calls] == ["第3章 标题3", "第1章 标题1", "第2章 标题2"]


async def test_config_snapshot_used_not_current_book_config(api):
    # AC-3.7
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    await enqueue(book_id)
    async with get_sessionmaker()() as s:
        book = await s.get(Book, uuid.UUID(book_id))
        book.run_config = {**book.run_config, "beam": 4}
        await s.commit()

    @dataclass
    class BeamSpy(FakeTranslator):
        beams: list = field(default_factory=list)

        def translate(self, texts, *, beam, batch_size):
            self.beams.append(beam)
            return super().translate(texts, beam=beam, batch_size=batch_size)

    spy = BeamSpy()
    await Worker(lambda: spy).run_once()
    assert spy.beams == [2]


async def test_pause_then_restart_resumes_unfinished_lines(api):
    # AC-3.5, BR-3.9
    book_id, chapters = await make_book(api, n_chapters=3, lines=4)
    await enqueue(book_id)
    fake1 = FakeTranslator()
    w1 = Worker(lambda: fake1, lines_per_step=2)
    assert await w1.run_once()  # chương 1 xong

    async def pause_after_first_step(step_no):
        if step_no == 1:
            async with get_sessionmaker()() as s:
                await queue.set_paused(s, "ct2", True)
                await s.commit()

    w1.on_step = pause_after_first_step
    assert await w1.run_once()  # chương 2: xong 1 bước rồi dừng
    async with get_sessionmaker()() as s:
        job2 = (await s.scalars(select(Job).where(Job.chapter_id == chapters[1].id))).one()
    assert (job2.status, job2.progress) == ("paused", 40)  # 2/5 dòng
    assert (await _get(Chapter, chapters[1].id)).status == "queued"

    # "tắt app": worker mới, model mới
    fake2 = FakeTranslator()
    w2 = Worker(lambda: fake2, lines_per_step=2)
    await w2.startup()
    assert await w2.run_once() is False  # đang tạm dừng thì không lấy job
    async with get_sessionmaker()() as s:
        await queue.set_paused(s, "ct2", False)
        await s.commit()
    assert await w2.run_once()
    assert sent(fake2) == ["第2章第2句话。", "第2章第3句话。", "第2章第4句话。"]  # chỉ phần còn lại
    assert (await _get(Chapter, chapters[0].id)).status == "translated"
    assert (await _get(Chapter, chapters[1].id)).status == "translated"
    assert len([s for s in await _segments(chapters[1].id) if not s.is_meta]) == 5


async def test_crash_mid_chapter_loses_nothing(api):
    # NFR-3, Review Focus 1
    book_id, chapters = await make_book(api, n_chapters=1, lines=4)
    (job_id,) = await enqueue(book_id)

    async def crash(step_no):
        if step_no == 2:
            raise SystemExit("kill -9")

    w1 = Worker(FakeTranslator, lines_per_step=2, on_step=crash)
    with pytest.raises(SystemExit):
        await w1.run_once()
    job = await _get(Job, job_id)
    assert job.status == "running"

    fake2 = FakeTranslator()
    w2 = Worker(lambda: fake2, lines_per_step=2)
    await w2.startup()
    assert await w2.run_once()
    assert sent(fake2) == ["第1章第4句话。"]
    assert (await _get(Chapter, chapters[0].id)).status == "translated"
    assert len([s for s in await _segments(chapters[0].id) if not s.is_meta]) == 5


async def test_cancel_running_job_discards_partial_work(api):
    # BR-3.10
    book_id, chapters = await make_book(api, n_chapters=1, lines=4)
    (job_id,) = await enqueue(book_id)

    async def cancel(step_no):
        if step_no == 1:
            async with get_sessionmaker()() as s:
                await queue.cancel_job(s, job_id)
                await s.commit()

    await Worker(FakeTranslator, lines_per_step=2, on_step=cancel).run_once()
    assert (await _get(Job, job_id)).status == "cancelled"
    assert (await _get(Chapter, chapters[0].id)).status == "todo"
    assert await _segments(chapters[0].id) == []
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(JobSegment))).all() == []


async def test_cancel_just_before_finish_does_not_write(api):
    # Review Focus 2: huỷ sau bước cuối, trước khi hoàn tất
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    (job_id,) = await enqueue(book_id)

    async def cancel_after_last(step_no):
        async with get_sessionmaker()() as s:
            await queue.cancel_job(s, job_id)
            await s.commit()

    await Worker(FakeTranslator, on_step=cancel_after_last).run_once()
    assert (await _get(Job, job_id)).status == "cancelled"
    assert await _segments(chapters[0].id) == []
    assert (await _get(Chapter, chapters[0].id)).status == "todo"


async def test_source_replaced_while_paused_restarts_chapter(api, data_dir):
    # Review Focus 3
    book_id, chapters = await make_book(api, n_chapters=1, lines=4)
    (job_id,) = await enqueue(book_id)

    async def pause(step_no):
        async with get_sessionmaker()() as s:
            await queue.set_paused(s, "ct2", True)
            await s.commit()

    await Worker(FakeTranslator, lines_per_step=2, on_step=pause).run_once()
    async with get_sessionmaker()() as s:
        book = await s.get(Book, uuid.UUID(book_id))
    src = data_dir / "books" / book.slug / "source" / "0001.txt"
    src.write_text("第1章 新标题\n新的一句。\n", encoding="utf-8")
    async with get_sessionmaker()() as s:
        await queue.set_paused(s, "ct2", False)
        await s.commit()
    fake = FakeTranslator()
    await Worker(lambda: fake, lines_per_step=2).run_once()
    assert sent(fake) == ["第1章 新标题", "新的一句。"]
    assert [s.src for s in await _segments(chapters[0].id)] == ["第1章 新标题", "新的一句。", ""]


async def test_failure_marks_error_and_continues(api):
    # BR-3.11
    book_id, chapters = await make_book(api, n_chapters=2, lines=1)
    await enqueue(book_id)

    class Exploding:
        model_id = "boom"

        def __init__(self):
            self.n = 0

        def translate(self, texts, *, beam, batch_size):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("tokenizer hỏng")
            return FakeTranslator().translate(texts, beam=beam, batch_size=batch_size)

    tr = Exploding()
    worker = Worker(lambda: tr)
    assert await worker.run_once() and await worker.run_once()
    first = await _get(Chapter, chapters[0].id)
    assert first.status == "error" and "tokenizer hỏng" in first.error
    assert (await _get(Chapter, chapters[1].id)).status == "translated"
    errors = await _logs(level="error")
    assert len(errors) == 1 and errors[0].detail["error"]["type"] == "RuntimeError"
    assert "Traceback" in errors[0].detail["error"]["stack"]
    assert tr.n == 2  # không tự thử lại lỗi thường


async def test_out_of_memory_halves_batch_up_to_twice(api):
    # BR-3.11
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    await enqueue(book_id)

    @dataclass
    class OomUntil2(FakeTranslator):
        sizes: list = field(default_factory=list)

        def translate(self, texts, *, beam, batch_size):
            self.sizes.append(batch_size)
            if batch_size > 8:
                raise MemoryError()
            return super().translate(texts, beam=beam, batch_size=batch_size)

    tr = OomUntil2()
    await Worker(lambda: tr).run_once()
    assert tr.sizes == [32, 16, 8]
    assert (await _get(Chapter, chapters[0].id)).status == "translated"
    warns = await _logs(level="warn")
    assert [w.message for w in warns if "bộ nhớ" in w.message] == [
        "Chương 1 · hết bộ nhớ, giảm batch 32 → 16", "Chương 1 · hết bộ nhớ, giảm batch 16 → 8",
    ]


async def test_out_of_memory_gives_up_after_two_retries(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    await enqueue(book_id)

    class AlwaysOom:
        model_id = "oom"

        def translate(self, texts, *, beam, batch_size):
            raise MemoryError()

    await Worker(AlwaysOom).run_once()
    assert (await _get(Chapter, chapters[0].id)).status == "error"


async def test_risky_flags_mean_needs_review_and_warn_log(api):
    # BR-0.1
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    await enqueue(book_id)

    class Truncating(FakeTranslator):
        def translate(self, texts, *, beam, batch_size):
            r = super().translate(texts, beam=beam, batch_size=batch_size)
            return BatchResult(r.outputs, r.tokens_in, r.tokens_out, [True] * len(texts))

    await Worker(Truncating).run_once()
    assert (await _get(Chapter, chapters[0].id)).status == "needs_review"
    line = (await _logs(source="translate"))[0]
    assert line.level == "warn"


async def test_retranslate_keeps_manual_edits_when_asked(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    await enqueue(book_id)
    await Worker(FakeTranslator).run_once()
    async with get_sessionmaker()() as s:
        await s.execute(
            update(Segment).where(Segment.chapter_id == chapters[0].id, Segment.idx == 3).values(dst="Câu tôi sửa", edited=True)
        )
        await s.commit()
    await enqueue(book_id, kind="retranslate", options={"keep_manual_edits": True})
    fake = FakeTranslator(prefix="V2")
    await Worker(lambda: fake).run_once()
    segs = {s.idx: s for s in await _segments(chapters[0].id)}
    assert segs[3].dst == "Câu tôi sửa" and segs[3].edited and segs[3].dst_machine == "V2<第1章第1句话。>"
    assert segs[4].dst == "V2<第1章第2句话。>"


async def test_startup_logs_model_load(api):
    worker = Worker(FakeTranslator)
    await worker.startup()
    system = await _logs(source="system")
    assert any(e.message.startswith("Nạp model fake") for e in system)


async def test_deepseek_jobs_not_taken_by_cpu_worker(api):
    book_id, _ = await make_book(api, n_chapters=1, lines=1)
    (job_id,) = await enqueue(book_id)
    async with get_sessionmaker()() as s:
        await s.execute(update(Job).where(Job.id == job_id).values(engine="deepseek"))
        await s.commit()
    assert await Worker(FakeTranslator).run_once() is False


async def test_run_forever_survives_loop_error(api, monkeypatch):
    # Một lỗi trong vòng lặp (vd mất kết nối DB) không được làm worker chết
    import asyncio

    from app import worker as worker_mod

    monkeypatch.setattr(worker_mod, "ERROR_BACKOFF_START", 0.01)
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    await enqueue(book_id)
    stop = asyncio.Event()
    worker = Worker(FakeTranslator)
    real_claim, real_run = worker.claim_next, worker.run_job
    calls = {"n": 0}

    async def flaky_claim():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("mất kết nối")
        return await real_claim()

    async def run_then_stop(job_id):
        await real_run(job_id)
        stop.set()

    monkeypatch.setattr(worker, "claim_next", flaky_claim)
    monkeypatch.setattr(worker, "run_job", run_then_stop)
    await asyncio.wait_for(worker.run_forever(stop), 10)
    assert calls["n"] >= 2
    assert (await _get(Chapter, chapters[0].id)).status == "translated"


async def test_run_forever_retries_startup(api, monkeypatch):
    import asyncio

    from app import worker as worker_mod

    monkeypatch.setattr(worker_mod, "ERROR_BACKOFF_START", 0.01)
    stop = asyncio.Event()
    worker = Worker(FakeTranslator)
    real_startup = worker.startup
    calls = {"n": 0}

    async def flaky_startup():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("DB chưa sẵn sàng")
        await real_startup()
        stop.set()

    monkeypatch.setattr(worker, "startup", flaky_startup)
    await asyncio.wait_for(worker.run_forever(stop), 10)
    assert calls["n"] == 2


async def test_run_job_swallows_context_load_and_fail_errors(api, monkeypatch):
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    (job_id,) = await enqueue(book_id)
    worker = Worker(FakeTranslator)

    class _Boom:
        def __call__(self):
            raise ConnectionError("mất kết nối")

    real_sessions = worker._sessions
    monkeypatch.setattr(worker, "_sessions", _Boom())
    await worker.run_job(job_id)  # nạp ngữ cảnh lỗi: ghi log rồi thoát, không ném ra
    monkeypatch.setattr(worker, "_sessions", real_sessions)

    async def bad(*a, **k):
        raise ConnectionError("lỗi khi dọn")

    monkeypatch.setattr(worker, "_load_translator", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(worker, "_fail", bad)
    await worker.run_job(job_id)  # _fail lỗi cũng không ném ra


async def test_recovery_cleans_job_cancelled_while_worker_down(api):
    # Huỷ job đang chạy rồi worker chết trước khi dọn: chương không được kẹt ở translating
    book_id, chapters = await make_book(api, n_chapters=1, lines=4)
    (job_id,) = await enqueue(book_id)
    w1 = Worker(FakeTranslator, lines_per_step=2)
    assert await w1.claim_next() == job_id
    async with get_sessionmaker()() as s:
        s.add(JobSegment(job_id=job_id, idx=0, dst="dở", flags=[]))
        await queue.cancel_job(s, job_id)
        await s.commit()
    assert (await _get(Chapter, chapters[0].id)).status == "translating"

    await Worker(FakeTranslator).startup()
    assert (await _get(Chapter, chapters[0].id)).status == "todo"
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(JobSegment))).all() == []


async def test_pause_racing_with_cancel_takes_cancel_branch(api, monkeypatch):
    from app.worker import JobInterrupted

    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    (job_id,) = await enqueue(book_id)

    async def cancel_then_pause(self, ctx):
        async with get_sessionmaker()() as s:
            s.add(JobSegment(job_id=ctx.job_id, idx=0, dst="dở", flags=[]))
            await queue.cancel_job(s, ctx.job_id)
            await s.commit()
        raise JobInterrupted("paused")

    monkeypatch.setattr(Worker, "_check_interrupt", cancel_then_pause)
    await Worker(FakeTranslator).run_once()
    assert (await _get(Job, job_id)).status == "cancelled"
    assert (await _get(Chapter, chapters[0].id)).status == "todo"
    async with get_sessionmaker()() as s:
        assert (await s.scalars(select(JobSegment))).all() == []


async def test_reenqueue_right_after_cancel_is_skipped_and_cleanup_restores_chapter(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    (job_id,) = await enqueue(book_id)
    worker = Worker(FakeTranslator)
    assert await worker.claim_next() == job_id
    async with get_sessionmaker()() as s:
        await queue.cancel_job(s, job_id)
        await s.commit()
    assert await enqueue(book_id) == []  # chương còn translating: bỏ qua
    await worker.run_job(job_id)  # worker dọn job đã huỷ
    assert (await _get(Chapter, chapters[0].id)).status == "todo"
    async with get_sessionmaker()() as s:
        assert len((await s.scalars(select(Job))).all()) == 1


async def test_reenqueue_after_cancel_cleanup_works(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    (job_id,) = await enqueue(book_id)
    worker = Worker(FakeTranslator)
    assert await worker.claim_next() == job_id
    async with get_sessionmaker()() as s:
        await queue.cancel_job(s, job_id)
        await s.commit()
    await worker.run_job(job_id)
    assert len(await enqueue(book_id)) == 1
    assert (await _get(Chapter, chapters[0].id)).status == "queued"


async def test_cancel_cleanup_keeps_chapter_when_other_active_job_exists(api):
    # job khác vẫn đang hoạt động cho chương: không trả trạng thái chương về cũ
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    (job_id,) = await enqueue(book_id)
    worker = Worker(FakeTranslator)
    assert await worker.claim_next() == job_id
    async with get_sessionmaker()() as s:
        await queue.cancel_job(s, job_id)
        s.add(Job(book_id=uuid.UUID(book_id), chapter_id=chapters[0].id, kind="translate", engine="ct2", status="queued",
                  progress=0, position=99, run_config={}, options={}, prev_chapter_status="todo",
                  tokens_in=0, tokens_out=0, attempts=0))
        await s.commit()
    await worker.run_job(job_id)
    assert (await _get(Chapter, chapters[0].id)).status == "translating"


class _OomFirstCallOfSteps:
    """Hết bộ nhớ ở lần gọi đầu tiên của các câu được chọn (mỗi câu = một bước khi lines_per_step=1)."""

    model_id = "oom"

    def __init__(self, fail_on: set[str]):
        self.pending = set(fail_on)
        self.sizes: list[int] = []

    def translate(self, texts, *, beam, batch_size):
        self.sizes.append(batch_size)
        hit = self.pending & set(texts)
        if hit:
            self.pending -= hit
            raise MemoryError()
        return FakeTranslator().translate(texts, beam=beam, batch_size=batch_size)


async def test_oom_retries_counted_across_whole_job_max_two(api):
    # BR-3.11: tối đa 2 lần giảm batch cho cả job, không phải mỗi bước
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)  # 3 câu dịch = 3 bước
    await enqueue(book_id)
    tr = _OomFirstCallOfSteps({"第1章 标题1", "第1章第1句话。", "第1章第2句话。"})
    await Worker(lambda: tr, lines_per_step=1).run_once()
    assert (await _get(Chapter, chapters[0].id)).status == "error"
    assert tr.sizes == [32, 16, 16, 8, 8]  # lần OOM thứ ba không còn được thử lại


async def test_oom_two_across_steps_still_succeeds(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    await enqueue(book_id)
    tr = _OomFirstCallOfSteps({"第1章 标题1", "第1章第1句话。"})
    await Worker(lambda: tr, lines_per_step=1).run_once()
    assert (await _get(Chapter, chapters[0].id)).status == "translated"
    assert tr.sizes == [32, 16, 16, 8, 8]


async def test_progress_event_has_full_job_payload(api):
    from app.services import events

    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    (job_id,) = await enqueue(book_id)
    broker = events.EventBroker()
    q = await broker.subscribe(None)
    try:
        await Worker(FakeTranslator, lines_per_step=1).run_once()
        await asyncio.sleep(0.3)
        running = []
        while not q.empty():
            ev = q.get_nowait()
            if ev["type"] == "job.updated" and ev["data"]["status"] == "running" and ev["data"]["progress"] not in (0, 100):
                running.append(ev["data"])
    finally:
        await broker.close()
    assert running
    async with get_sessionmaker()() as s:
        expected_keys = set(events.job_payload(await s.get(Job, job_id)))
    assert set(running[0]) == expected_keys
    assert running[0]["id"] == str(job_id) and running[0]["tokens_in"] > 0


async def test_machine_revision_has_snapshot(api):
    book_id, chapters = await make_book(api, n_chapters=1, lines=2)
    await enqueue(book_id)
    await Worker(FakeTranslator).run_once()
    async with get_sessionmaker()() as s:
        rev = (await s.scalars(select(ChapterRevision))).one()
    assert [r["idx"] for r in rev.snapshot] == [0, 3, 4]
    assert rev.snapshot[1] == {"idx": 3, "src": "第1章第1句话。", "dst": "VI<第1章第1句话。>",
                               "dst_machine": "VI<第1章第1句话。>", "edited": False}
    assert rev.changed_idx == [] and rev.note is None
