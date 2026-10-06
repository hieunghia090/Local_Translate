import statistics
import time

import pytest

from app.devtools.seed_perf import seed_perf

pytestmark = pytest.mark.db
PAGE_BUDGET_S = 1.0   # NFR-2: tải trang
QUERY_BUDGET_S = 0.2  # NFR-2: lọc, tìm
RUNS = 5


async def median_s(api, url: str) -> float:
    times = []
    for _ in range(RUNS + 1):
        t0 = time.perf_counter()
        r = await api.get(url)
        times.append(time.perf_counter() - t0)
        assert r.status_code == 200, f"{url}: {r.text}"
    return statistics.median(times[1:])  # bỏ lần đầu: làm nóng kết nối và cache


async def test_nfr2_library_50_books_workspace_2000_chapters(api, capsys):
    big = await seed_perf()
    stats = (await api.get("/api/v1/library/stats")).json()
    assert stats["books"] == 50 and stats["chapters_total"] == 2000 + 49 * 40

    # Các request trang gửi khi mở. Cộng dồn tuần tự, chặt hơn trình duyệt (gửi song song).
    library = ["/api/v1/books?sort=recent", "/api/v1/library/stats"]
    workspace = [f"/api/v1/books/{big}", f"/api/v1/books/{big}/chapters?limit=50",
                 f"/api/v1/queue?book_id={big}", f"/api/v1/books/{big}/logs/summary"]
    library_s = sum([await median_s(api, u) for u in library])
    workspace_s = sum([await median_s(api, u) for u in workspace])
    queries = {
        "lọc Lỗi": f"/api/v1/books/{big}/chapters?status=error&limit=50",
        "lọc Đã dịch": f"/api/v1/books/{big}/chapters?status=translated,reviewed&limit=50",
        "tìm tên Việt không dấu": f"/api/v1/books/{big}/chapters?q=tieu%20de%20thu%201999&limit=50",
        "tìm số chương": f"/api/v1/books/{big}/chapters?q=1999&limit=50",
        "tìm chữ Hán": f"/api/v1/books/{big}/chapters?q=测试标题15&limit=50",
        "tìm truyện": "/api/v1/books?q=hieu%20nang%20049",
        "lọc truyện": "/api/v1/books?filter=in_progress",
    }
    times = {name: await median_s(api, url) for name, url in queries.items()}
    with capsys.disabled():
        print(f"\nNFR-2 · thư viện {library_s * 1000:.0f} ms · workspace {workspace_s * 1000:.0f} ms · "
              + " · ".join(f"{k} {v * 1000:.0f} ms" for k, v in times.items()))
    assert library_s < PAGE_BUDGET_S and workspace_s < PAGE_BUDGET_S
    slow = {k: round(v * 1000) for k, v in times.items() if v >= QUERY_BUDGET_S}
    assert not slow, slow
