import time
import uuid

import pytest
from sqlalchemy import func, select, update

from app.core.translator import BatchResult, FakeTranslator
from app.db import get_sessionmaker
from app.models import Book, Chapter, Job, LogEntry, Segment
from app.worker import Worker
from helpers import enqueue, make_book

pytestmark = pytest.mark.db


class Scripted(FakeTranslator):
    """Trả bản dịch cố định theo câu nguồn; câu không có trong bảng thì dùng FakeTranslator."""

    def __init__(self, table: dict[str, str]):
        super().__init__()
        self.model_id = "HachimiMT-60"
        self.table = table

    def translate(self, texts, *, beam, batch_size):
        r = super().translate(texts, beam=beam, batch_size=batch_size)
        return BatchResult([self.table.get(t, o) for t, o in zip(texts, r.outputs)], r.tokens_in, r.tokens_out, r.truncated)


async def book_with_lines(api, lines: list[str], genre="xianxia"):
    from helpers import upload

    text = "第1章 测试\n" + "\n".join(lines) + "\n" + "宗门修为灵石江湖丹田" * 3  # đủ tín hiệu cổ trang
    view = await upload(api, [("0001.txt", text.encode())])
    await api.patch(f"/api/v1/imports/{view['import_id']}", json={"chapters": [{"key": "c0001", "selected": True}]})
    r = await api.post("/api/v1/books", json={"title_zh": "测", "title_vi": "Thử", "genre": genre, "import_id": view["import_id"],
                                              "confirm_duplicate": True})
    book_id = r.json()["id"]
    async with get_sessionmaker()() as s:
        ch = (await s.scalars(select(Chapter).where(Chapter.book_id == uuid.UUID(book_id)))).one()
    return book_id, ch


async def test_worker_normalizes_and_keeps_raw(api):
    # AC-7.1 qua worker; tác dụng phụ: lưu bản thô, edits, cờ, route
    book_id, ch = await book_with_lines(api, ["师姐端着一碗药走了进来。"])
    await enqueue(book_id)
    await Worker(lambda: Scripted({"师姐端着一碗药走了进来。": "Chị bưng một bát thuốc đi vào."})).run_once()
    async with get_sessionmaker()() as s:
        seg = (await s.scalars(select(Segment).where(Segment.chapter_id == ch.id, Segment.idx == 1))).one()
        chapter = await s.get(Chapter, ch.id)
        line = (await s.scalars(select(LogEntry).where(LogEntry.source == "translate"))).one()
    assert seg.dst_model_raw == "Chị bưng một bát thuốc đi vào."
    assert seg.dst == seg.dst_machine == "Sư tỷ bưng một bát thuốc đi vào."
    assert seg.honorific_edits[0]["rule"] == "kinship.师姐" and "honorific_rewritten" in seg.flags
    assert chapter.register_route == "ancient" and chapter.status == "translated"  # cờ này không đẩy sang Cần soát
    assert "có cờ" not in line.message and line.detail["segments"]["flagged"] == 0  # cờ honorific_rewritten không tính
    assert line.detail["honorific"]["route"] == "ancient" and line.detail["honorific"]["applied"]["kinship"] == 1


async def test_chapter_view_exposes_edits_and_route(api):
    book_id, ch = await book_with_lines(api, ["师姐端着一碗药走了进来。"])
    await enqueue(book_id)
    await Worker(lambda: Scripted({"师姐端着一碗药走了进来。": "Chị bưng một bát thuốc đi vào."})).run_once()
    body = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()
    assert body["chapter"]["register_route"] == "ancient" and body["chapter"]["register_override"] is None
    assert body["segments"][1]["honorific_edits"][0]["to"] == "Sư tỷ"


async def test_cpu_worker_ignores_reapply_jobs(api):
    # Review Focus 4
    book_id, ch = await book_with_lines(api, ["他来了。"])
    async with get_sessionmaker()() as s:
        s.add(Job(book_id=uuid.UUID(book_id), chapter_id=ch.id, kind="honorific_reapply", engine="ct2", status="queued",
                  position=1, run_config={}))
        await s.commit()
    assert await Worker(FakeTranslator).run_once() is False


async def test_reapply_after_enabling_pronoun_keeps_manual_edits(api):
    # AC-7.8, BR-7.14, Review Focus 3
    lines = ["他来了。", "她笑了。"]
    book_id, ch = await book_with_lines(api, lines)
    await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"honorific": {"kinship": True, "pronoun": False}}})
    await enqueue(book_id)
    await Worker(lambda: Scripted({"他来了。": "Anh ta tới rồi.", "她笑了。": "Cô ấy cười."})).run_once()
    await api.patch(f"/api/v1/segments/{ch.id}/2", json={"dst": "Nàng ấy cười (tôi sửa)."})
    await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"honorific": {"pronoun": True}}})
    async with get_sessionmaker()() as s:
        jobs_before = await s.scalar(select(func.count()).select_from(Job))
    t0 = time.perf_counter()
    r = await api.post(f"/api/v1/books/{book_id}/honorific/reapply", json={})
    assert r.status_code == 202 and r.json()["chapters"] == 1
    assert time.perf_counter() - t0 < 1.0
    segs = {x["idx"]: x for x in (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()["segments"]}
    assert segs[1]["dst"] == "Hắn tới rồi."
    assert segs[2]["dst"] == "Nàng ấy cười (tôi sửa)."  # câu sửa tay không bị áp lại
    async with get_sessionmaker()() as s:
        kinds = (await s.scalars(select(Job.kind).where(Job.kind != "translate"))).all()
        assert await s.scalar(select(func.count()).select_from(Job)) == jobs_before + 1
    assert kinds == ["honorific_reapply"]
    revs = (await api.get(f"/api/v1/chapters/{ch.id}/revisions")).json()
    assert revs[0]["note"] == "honorific_reapply"


async def test_force_modern_route_removes_pronoun_edits(api):
    # AC-7.9
    book_id, ch = await book_with_lines(api, ["他来了。"])
    await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"honorific": {"pronoun": True}}})
    await enqueue(book_id)
    await Worker(lambda: Scripted({"他来了。": "Anh ta tới rồi."})).run_once()
    seg = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()["segments"][1]
    assert seg["dst"] == "Hắn tới rồi."
    r = await api.put(f"/api/v1/chapters/{ch.id}/register", json={"route": "modern"})
    assert r.json()["register_override"] == "modern" and r.json()["job_id"]
    body = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()
    assert body["segments"][1]["dst"] == "Anh ta tới rồi." and body["segments"][1]["honorific_edits"] == []
    assert body["chapter"]["register_route"] == "modern"
    r = await api.put(f"/api/v1/chapters/{ch.id}/register", json={"route": None})
    assert r.json()["register_override"] is None


async def test_reapply_old_chapter_without_raw_uses_machine_text(api):
    # Review Focus 3: chương dịch trước G7
    book_id, ch = await book_with_lines(api, ["他来了。"])
    await enqueue(book_id)
    await Worker(lambda: Scripted({"他来了。": "Anh ta tới rồi."})).run_once()
    async with get_sessionmaker()() as s:
        await s.execute(update(Segment).where(Segment.chapter_id == ch.id).values(dst_model_raw=None))
        await s.commit()
    await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"honorific": {"pronoun": True}}})
    await api.post(f"/api/v1/books/{book_id}/honorific/reapply", json={})
    seg = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()["segments"][1]
    assert seg["dst"] == "Hắn tới rồi."


async def test_reapply_nothing(api):
    book_id, _ = await book_with_lines(api, ["他来了。"])
    r = await api.post(f"/api/v1/books/{book_id}/honorific/reapply", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOTHING_TO_REAPPLY"


async def test_stale_reapply_job_failed_on_startup(api):
    # Review Focus 4
    from app.services import honorific

    book_id, ch = await book_with_lines(api, ["他来了。"])
    async with get_sessionmaker()() as s:
        s.add(Job(book_id=uuid.UUID(book_id), chapter_id=None, kind="honorific_reapply", engine="ct2", status="running",
                  position=1, run_config={}))
        await s.commit()
        assert await honorific.fail_stale_reapply_jobs(s) == 1
        await s.commit()
        assert (await s.scalars(select(Job.status))).one() == "failed"


async def test_preview(api):
    r = await api.post("/api/v1/honorific/preview", json={
        "src": "“师尊，我错了。”", "dst_raw": "“Sư tôn, tôi sai rồi.”", "route": "ancient", "config": {"pronoun": True}})
    assert r.json()["dst"] == "“Sư tôn, đệ tử sai rồi.”"  # AC-7.3


async def test_register_override_read_at_finish(api):
    # Fix 3: đổi route giữa lúc job đang chạy thì kết quả dùng route mới
    from app.services import honorific

    book_id, ch = await book_with_lines(api, ["他来了。"])
    await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"honorific": {"pronoun": True}}})
    await enqueue(book_id)

    async def change_override(step_no: int) -> None:  # sau khi job đã nhận, trước khi ghi kết quả
        async with get_sessionmaker()() as s:
            await honorific.set_register(s, ch.id, "modern")

    await Worker(lambda: Scripted({"他来了。": "Anh ta tới rồi."}), on_step=change_override).run_once()
    async with get_sessionmaker()() as s:
        chapter = await s.get(Chapter, ch.id)
        seg = (await s.scalars(select(Segment).where(Segment.chapter_id == ch.id, Segment.idx == 1))).one()
    assert chapter.register_route == "modern" and seg.dst == "Anh ta tới rồi."


async def test_only_ct2_output_is_normalized(api):
    # Fix 4: job deepseek giữ nguyên bản dịch, không có edits; reapply bỏ qua chương không phải HachimiMT
    book_id, ch = await book_with_lines(api, ["他来了。"])
    r = await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"engine": "deepseek", "model_id": "deepseek-flash",
                                                                          "honorific": {"pronoun": True}}})
    assert r.status_code == 200, r.text
    await enqueue(book_id)
    ds = Scripted({"他来了。": "Anh ta tới rồi."})
    ds.model_id = "deepseek-flash"
    assert await Worker(lambda: ds, engine="deepseek").run_once()
    async with get_sessionmaker()() as s:
        seg = (await s.scalars(select(Segment).where(Segment.chapter_id == ch.id, Segment.idx == 1))).one()
        chapter = await s.get(Chapter, ch.id)
    assert seg.dst == seg.dst_machine == seg.dst_model_raw == "Anh ta tới rồi."
    assert seg.honorific_edits == [] and "honorific_rewritten" not in seg.flags and chapter.status in ("translated", "needs_review")
    r = await api.post(f"/api/v1/books/{book_id}/honorific/reapply", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOTHING_TO_REAPPLY"


async def test_reapply_stops_when_cancelled_and_never_overwrites_cancelled(api, monkeypatch):
    # Fix 6
    from app.services import honorific, queue

    book_id, chapters = await make_book(api, n_chapters=2)
    async with get_sessionmaker()() as s:
        await s.execute(update(Chapter).where(Chapter.book_id == uuid.UUID(book_id)).values(status="translated", model_id=None))
        await s.commit()
        job, ids = await honorific.start_reapply(s, uuid.UUID(book_id), None)
    calls = []
    real = honorific._reapply_one

    async def one_then_cancel(session, book, chapter):
        calls.append(chapter.no)
        await real(session, book, chapter)
        async with get_sessionmaker()() as s2:
            await queue.cancel_job(s2, job.id)
            await s2.commit()

    monkeypatch.setattr(honorific, "_reapply_one", one_then_cancel)
    await honorific.reapply_chapters(uuid.UUID(book_id), ids, job.id)
    async with get_sessionmaker()() as s:
        got = await s.get(Job, job.id)
    assert calls == [1] and got.status == "cancelled"


async def test_list_queue_ignores_reapply_jobs(api):
    # Fix 6
    from datetime import timedelta

    from app.models import utcnow
    from app.services import queue

    book_a, _ = await book_with_lines(api, ["他来了。"])
    book_b = (await make_book(api, n_chapters=1, title="乙"))[0]
    now = utcnow()
    async with get_sessionmaker()() as s:
        s.add(Job(book_id=uuid.UUID(book_b), kind="honorific_reapply", engine="ct2", status="running", position=0,
                  run_config={}, started_at=now))
        s.add(Job(book_id=uuid.UUID(book_b), kind="honorific_reapply", engine="ct2", status="done", position=0,
                  run_config={}, started_at=now - timedelta(seconds=100), finished_at=now))
        await s.commit()
        view = await queue.list_queue(s, uuid.UUID(book_a))
    assert view["running_elsewhere"] is None and view["eta_seconds"] is None


TABLE2 = {"他来了。": "Anh ta tới rồi.", "她笑了。": "Cô ấy cười."}


async def test_worker_refreshes_machine_for_edited_segments_without_flag(api):
    # Fix 7 (worker, keep_manual_edits)
    book_id, ch = await book_with_lines(api, ["他来了。", "她笑了。"])
    await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"honorific": {"pronoun": True}}})
    await enqueue(book_id)
    await Worker(lambda: Scripted(TABLE2)).run_once()
    await api.patch(f"/api/v1/segments/{ch.id}/2", json={"dst": "Nàng ấy cười (tôi sửa)."})
    await enqueue(book_id, kind="retranslate", options={"keep_manual_edits": True})
    await Worker(lambda: Scripted(TABLE2)).run_once()
    async with get_sessionmaker()() as s:
        segs = {x.idx: x for x in (await s.scalars(select(Segment).where(Segment.chapter_id == ch.id))).all()}
    assert segs[1].dst == "Hắn tới rồi." and "honorific_rewritten" in segs[1].flags
    e = segs[2]
    assert e.edited and e.dst == "Nàng ấy cười (tôi sửa)."
    assert e.dst_machine == "Nàng cười." and e.dst_model_raw == "Cô ấy cười." and e.honorific_edits
    assert "honorific_rewritten" not in e.flags


async def test_reapply_refreshes_machine_for_edited_segments_without_flag(api):
    # Fix 7 (reapply)
    book_id, ch = await book_with_lines(api, ["他来了。", "她笑了。"])
    await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"honorific": {"kinship": True, "pronoun": False}}})
    await enqueue(book_id)
    await Worker(lambda: Scripted(TABLE2)).run_once()
    await api.patch(f"/api/v1/segments/{ch.id}/2", json={"dst": "Nàng ấy cười (tôi sửa)."})
    await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"honorific": {"pronoun": True}}})
    await api.post(f"/api/v1/books/{book_id}/honorific/reapply", json={})
    async with get_sessionmaker()() as s:
        e = (await s.scalars(select(Segment).where(Segment.chapter_id == ch.id, Segment.idx == 2))).one()
    assert e.dst == "Nàng ấy cười (tôi sửa)."
    assert e.dst_machine == "Nàng cười." and e.dst_model_raw == "Cô ấy cười." and e.honorific_edits
    assert "honorific_rewritten" not in e.flags


async def test_reapply_updates_dst_mt_like_dst_machine(api):
    # BR-6.12: áp lại xưng hô cập nhật dst_mt cả với câu sửa tay
    lines = ["他来了。", "她笑了。"]
    book_id, ch = await book_with_lines(api, lines)
    await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"honorific": {"kinship": True, "pronoun": False}}})
    await enqueue(book_id)
    await Worker(lambda: Scripted({"他来了。": "Anh ta tới rồi.", "她笑了。": "Cô ấy cười."})).run_once()
    await api.patch(f"/api/v1/segments/{ch.id}/2", json={"dst": "Nàng ấy cười (tôi sửa)."})
    await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"honorific": {"pronoun": True}}})
    r = await api.post(f"/api/v1/books/{book_id}/honorific/reapply", json={})
    assert r.status_code == 202
    async with get_sessionmaker()() as s:
        segs = {x.idx: x for x in (await s.scalars(select(Segment).where(Segment.chapter_id == ch.id))).all()}
    assert segs[1].dst == segs[1].dst_machine == segs[1].dst_mt == "Hắn tới rồi."
    assert segs[2].dst == "Nàng ấy cười (tôi sửa)." and segs[2].dst_mt == segs[2].dst_machine
    assert all(x.dst_ai is None for x in segs.values())
