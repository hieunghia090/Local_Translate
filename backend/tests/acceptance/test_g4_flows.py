import pytest

from app.core.translator import FakeTranslator
from app.worker import Worker
from helpers import make_book

pytestmark = pytest.mark.db


async def run_all(fake):
    worker = Worker(lambda: fake)
    while await worker.run_once():
        pass


async def test_retranslate_keeping_two_manual_edits(api):
    # AC-3.6, AC-6.4
    book_id, chapters = await make_book(api, n_chapters=1, lines=4)
    cid = str(chapters[0].id)
    await api.post(f"/api/v1/books/{book_id}/chapters/bulk", json={"action": "translate", "chapter_ids": [cid]})
    await run_all(FakeTranslator())
    await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "Sửa một"})
    await api.patch(f"/api/v1/segments/{cid}/5", json={"dst": "Sửa hai"})
    r = await api.post(f"/api/v1/chapters/{cid}/translate", json={"keep_manual_edits": True})
    assert r.status_code == 202
    await run_all(FakeTranslator(prefix="V2"))
    segs = (await api.get(f"/api/v1/books/{book_id}/chapters/by-no/1")).json()["segments"]
    assert segs[3]["dst"] == "Sửa một" and segs[5]["dst"] == "Sửa hai"
    assert segs[4]["dst"].startswith("V2<") and segs[6]["dst"].startswith("V2<")


async def test_book_config_change_affects_only_new_jobs(api):
    # AC-3.7
    book_id, chapters = await make_book(api, n_chapters=2, lines=1)
    await api.post(f"/api/v1/books/{book_id}/chapters/bulk",
                   json={"action": "translate", "chapter_ids": [str(chapters[0].id)]})
    await api.patch(f"/api/v1/books/{book_id}", json={"run_config": {"beam": 4}})
    await api.post(f"/api/v1/books/{book_id}/chapters/bulk",
                   json={"action": "translate", "chapter_ids": [str(chapters[1].id)]})

    beams = []

    class Spy(FakeTranslator):
        def translate(self, texts, *, beam, batch_size):
            beams.append(beam)
            return super().translate(texts, beam=beam, batch_size=batch_size)

    await run_all(Spy())
    assert beams == [2, 4]


async def test_review_cycle(api):
    # AC-6.2, AC-6.3 trên một luồng
    book_id, chapters = await make_book(api, n_chapters=1, lines=1)
    cid = str(chapters[0].id)
    await api.post(f"/api/v1/books/{book_id}/chapters/bulk", json={"action": "translate", "filter": {"status": ["todo"]}})
    await run_all(FakeTranslator())
    assert (await api.post(f"/api/v1/chapters/{cid}/mark-reviewed")).json()["status"] == "reviewed"
    r = await api.patch(f"/api/v1/segments/{cid}/3", json={"dst": "Sửa"})
    assert r.json()["chapter"]["status"] == "needs_review"
    kinds = [rev["kind"] for rev in (await api.get(f"/api/v1/chapters/{cid}/revisions")).json()]
    assert kinds == ["manual", "machine"]
    detail = (await api.get(f"/api/v1/books/{book_id}")).json()
    assert detail["stats"]["needs_review"] == 1
