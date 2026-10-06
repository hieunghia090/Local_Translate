import time
from pathlib import Path

import pytest

from app.core.titles import strip_ads
from app.services.import_analysis import StoredFile, analyze, default_title
from helpers import wait_ready

SOURCE = Path(__file__).resolve().parents[3] / "大宋有种--35466"
needs_source = pytest.mark.skipif(not SOURCE.is_dir(), reason="thiếu thư mục 大宋有种--35466")


@pytest.mark.db
@needs_source
async def test_import_whole_folder_follows_rules_under_60s(api, data_dir, fake_translator):
    # AC-2.1, AC-2.3, AC-2.9 kiểm tra theo quy tắc (BR-2.3, 2.4, 2.5, 2.9), không cài cứng số chương
    paths = sorted(SOURCE.iterdir())
    text_files = [p for p in paths if p.suffix.lower() in (".txt", ".md")]
    numbered = sorted((p for p in text_files if p.name[:1].isdigit()), key=lambda p: int(p.name.split(" ")[0]))
    t0 = time.perf_counter()
    r = await api.post(
        "/api/v1/imports",
        data={"mode": "multi", "source_name": SOURCE.name},
        files=[("files[]", (f"{SOURCE.name}/{p.name}", p.read_bytes(), "text/plain")) for p in paths],
    )
    assert r.status_code == 202, r.text
    view = await wait_ready(api, r.json()["import_id"], timeout=60)
    assert view["status"] == "ready"
    assert view["suggested_title_zh"] == "大宋有种"

    rows = view["chapters"]
    # BR-2.9: mỗi file .txt/.md là một dòng, file loại khác chỉ nằm ở file_errors
    assert len(rows) == len(text_files)
    rejected = sorted(e["file"].rsplit("/", 1)[-1] for e in view["file_errors"])
    assert rejected == sorted(p.name for p in paths if p not in text_files)
    assert {e["code"] for e in view["file_errors"]} <= {"UNSUPPORTED_TYPE"}
    # BR-2.4: file có số đứng trước, đúng thứ tự số; tiêu đề là dòng đầu của file
    for row, path in zip(rows, numbered):
        first_line = next(l.strip() for l in path.read_text(encoding="utf-8").split("\n") if l.strip())
        assert row["title_zh"] == first_line[:200]
    # BR-2.3: dòng bị bỏ chọn sẵn luôn có lý do, dòng có lý do thì bị bỏ chọn
    for row in rows:
        flagged = bool({"SHORT", "AUTHOR_NOTE"} & set(row["warnings"]))
        assert row["selected"] is not flagged, row
    # BR-2.5 / AC-2.3: không câu nào gửi vào model còn đuôi quảng cáo.
    # Không kiểm tra chữ 求 đơn lẻ: nó có trong tiêu đề thật như 卖国求饶, 赵构求见.
    sent = [t for call in fake_translator.calls for t in call]
    assert sent and all(strip_ads(t) == t for t in sent)

    r = await api.post("/api/v1/books", json={"title_zh": "大宋有种", "title_vi": "Đại Tống Hữu Chủng",
                                              "genre": "modern_war", "import_id": view["import_id"]})
    assert r.status_code == 201, r.text
    elapsed = time.perf_counter() - t0
    source = data_dir / "books" / "dai-tong-huu-chung" / "source"
    files = sorted(source.iterdir())
    assert len(files) == sum(row["selected"] for row in rows)  # AC-2.9
    for f in files:
        f.read_bytes().decode("utf-8")
    skipped = [(row["no"], row["title_zh"], row["warnings"]) for row in rows if not row["selected"]]
    print(f"\nImport {len(paths)} file + tạo truyện: {elapsed:.1f}s · {len(rows)} dòng · bỏ chọn sẵn: {skipped}")
    assert elapsed < 60


@pytest.mark.slow
@needs_source
def test_real_title_translation_speed():
    from app.core.ct2_translator import CT2Translator

    files = [StoredFile(p.name, str(p), p.stat().st_size) for p in sorted(SOURCE.iterdir())]
    translator = CT2Translator(threads=4)
    t0 = time.perf_counter()
    a = analyze(files, mode="multi", translator=translator, source_name=SOURCE.name)
    elapsed = time.perf_counter() - t0
    c16 = a.chapters[15].to_json()
    print(f"\nPhân tích + dịch {len(a.chapters)} tiêu đề: {elapsed:.1f}s · ch.16: {default_title(c16)}")
    assert c16["title_machine"] and "TITLE_UNTRANSLATED" not in c16["warnings"]
    assert elapsed < 60
