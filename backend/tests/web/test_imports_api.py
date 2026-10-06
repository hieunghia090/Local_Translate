import shutil
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import delete, update

from app.db import get_sessionmaker
from app.main import create_app
from app.models import ImportSession
from app.services import imports
from helpers import upload, wait_ready

pytestmark = pytest.mark.db

BODY = "他走了很远的路，终于到了。" * 20


def chapter(title: str) -> bytes:
    return f"{title}\n=========\nNguồn: https://x\n\n{BODY}\n".encode()


THREE = [
    ("0003 - c.txt", chapter("第3章 标题三")),
    ("0001 - a.txt", chapter("第1章 标题一（求收藏）")),
    ("0002 - b.txt", chapter("第2章 标题二")),
]


async def _set(import_id: str, **values) -> None:
    async with get_sessionmaker()() as s:
        await s.execute(update(ImportSession).where(ImportSession.id == uuid.UUID(import_id)).values(**values))
        await s.commit()


async def test_multi_upload_preview(api):
    # AC-2.1 (bản nhỏ), AC-2.3
    view = await upload(api, THREE, source_name="大宋有种--35466")
    assert view["status"] == "ready"
    assert view["suggested_title_zh"] == "大宋有种"
    assert [c["title_vi"] for c in view["chapters"]] == [
        "Chương 1: VI<标题一>", "Chương 2: VI<标题二>", "Chương 3: VI<标题三>",
    ]
    assert all(c["selected"] and not c["title_vi_edited"] for c in view["chapters"])
    assert view["total_chars"] == sum(c["chars"] for c in view["chapters"])


async def test_folder_name_from_relative_paths(api):
    view = await upload(api, [(f"大宋有种--35466/{n}", b) for n, b in THREE])
    assert view["suggested_title_zh"] == "大宋有种"


async def test_path_in_filename_never_escapes_import_dir(api, data_dir):
    # Review Focus 4
    view = await upload(api, [("../../evil.txt", chapter("第1章 甲"))])
    assert view["status"] == "ready" and len(view["chapters"]) == 1
    raw = imports.import_dir(uuid.UUID(view["import_id"])) / "raw"
    assert [p.name for p in raw.iterdir()] == ["00000"]
    assert not any(p.name == "evil.txt" for p in data_dir.parent.rglob("*"))


async def test_single_mode_requires_exactly_one_file(api):
    r = await api.post("/api/v1/imports", data={"mode": "single"},
                       files=[("files[]", (n, b, "text/plain")) for n, b in THREE])
    assert r.status_code == 422 and r.json()["error"]["code"] == "SINGLE_MODE_ONE_FILE"


@pytest.mark.parametrize(
    "data,code",
    [
        ({"mode": "zip"}, "INVALID_MODE"),
        ({"mode": "single", "split_rule": "regex", "split_regex": "("}, "INVALID_REGEX"),
        ({"mode": "single", "split_rule": "regex"}, "INVALID_REGEX"),
        ({"mode": "single", "encoding": "latin-1"}, "UNSUPPORTED_ENCODING"),
    ],
)
async def test_invalid_params(api, data, code):
    r = await api.post("/api/v1/imports", data=data, files=[("files[]", ("a.txt", chapter("第1章 甲"), "text/plain"))])
    assert r.status_code == 422 and r.json()["error"]["code"] == code


async def test_no_files(api):
    r = await api.post("/api/v1/imports", data={"mode": "multi"}, files=[("other", ("x", b"", "text/plain"))])
    assert r.status_code == 422 and r.json()["error"]["code"] == "NO_FILES"


async def test_too_many_files(api, monkeypatch):
    monkeypatch.setattr(imports, "MAX_FILES", 3)
    files = [("files[]", (f"{i}.txt", chapter("第1章 甲"), "text/plain")) for i in range(4)]
    r = await api.post("/api/v1/imports", data={"mode": "multi"}, files=files)
    assert r.status_code == 422 and r.json()["error"]["code"] == "TOO_MANY_FILES"


async def test_file_too_large_reported_and_not_kept(api, monkeypatch):
    monkeypatch.setattr(imports, "MAX_FILE_BYTES", 100)
    view = await upload(api, [("0001.txt", chapter("第1章 甲"))])
    assert [e["code"] for e in view["file_errors"]] == ["FILE_TOO_LARGE"]
    assert list((imports.import_dir(uuid.UUID(view["import_id"])) / "raw").iterdir()) == []


async def test_forced_utf8_on_gbk_file_reports_that_file(api):
    # AC-2.6
    gbk = ("第1章 开始\n" + BODY).encode("gbk")
    view = await upload(api, [("gbk.txt", gbk), ("0002.txt", chapter("第2章 乙"))], encoding="utf-8")
    assert [(e["file"], e["code"]) for e in view["file_errors"]] == [("gbk.txt", "ENCODING")]
    assert len(view["chapters"]) == 1
    auto = await upload(api, [("gbk.txt", gbk)])
    assert auto["chapters"][0]["title_zh"] == "第1章 开始"


async def test_edits_survive_reanalysis(api):
    # AC-2.4
    view = await upload(api, THREE)
    r = await api.patch(
        f"/api/v1/imports/{view['import_id']}",
        json={"chapters": [{"key": "c0003", "title_vi": "Tên tự sửa"}, {"key": "c0001", "selected": False}]},
    )
    assert r.status_code == 200
    assert r.json()["chapters"][2]["title_vi"] == "Tên tự sửa"
    r = await api.patch(f"/api/v1/imports/{view['import_id']}", json={"encoding": "utf-8"})
    assert r.status_code == 200 and r.json()["status"] == "parsing"
    after = await wait_ready(api, view["import_id"])
    assert after["status"] == "ready" and after["encoding"] == "utf-8"
    assert after["chapters"][2]["title_vi"] == "Tên tự sửa" and after["chapters"][2]["title_vi_edited"]
    assert after["chapters"][0]["selected"] is False


async def test_chapter_files_match_view_when_reparse_turns_ready(api):
    view = await upload(api, THREE)
    r = await api.patch(f"/api/v1/imports/{view['import_id']}", json={"encoding": "utf-8"})
    assert r.status_code == 200 and r.json()["status"] == "parsing"
    after = await wait_ready(api, view["import_id"])
    assert after["status"] == "ready"
    iid = uuid.UUID(view["import_id"])
    assert after["chapters"]
    for c in after["chapters"]:
        assert imports.chapter_text_path(iid, c["key"]).is_file()
    assert not list(imports.import_dir(iid).glob("chapters-*"))


async def test_blank_title_restores_default(api):
    view = await upload(api, THREE)
    url = f"/api/v1/imports/{view['import_id']}"
    await api.patch(url, json={"chapters": [{"key": "c0002", "title_vi": "Khác"}]})
    r = await api.patch(url, json={"chapters": [{"key": "c0002", "title_vi": "  "}]})
    assert r.json()["chapters"][1]["title_vi"] == "Chương 2: VI<标题二>"


async def test_reanalysis_rejected_while_parsing(api):
    view = await upload(api, THREE)
    await _set(view["import_id"], status="parsing")
    url = f"/api/v1/imports/{view['import_id']}"
    r = await api.patch(url, json={"encoding": "gbk"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "IMPORT_BUSY"
    r = await api.patch(url, json={"chapters": [{"key": "c0001", "selected": False}]})
    assert r.status_code == 200


async def test_stale_parsing_becomes_failed_and_can_rerun(api):
    # Review Focus 3
    view = await upload(api, THREE)
    old = datetime.now(timezone.utc) - timedelta(minutes=11)
    await _set(view["import_id"], status="parsing", updated_at=old)
    body = (await api.get(f"/api/v1/imports/{view['import_id']}")).json()
    assert body["status"] == "failed" and body["error"]
    r = await api.patch(f"/api/v1/imports/{view['import_id']}", json={"encoding": "auto"})
    assert r.status_code == 200
    assert (await wait_ready(api, view["import_id"]))["status"] == "ready"


async def test_expired_import_is_purged(api):
    view = await upload(api, THREE)
    await _set(view["import_id"], expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    await upload(api, THREE)  # POST mới sẽ dọn phiên hết hạn
    r = await api.get(f"/api/v1/imports/{view['import_id']}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "IMPORT_NOT_FOUND"
    assert not imports.import_dir(uuid.UUID(view["import_id"])).exists()


async def test_unknown_import_404(api):
    r = await api.get(f"/api/v1/imports/{uuid.uuid4()}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "IMPORT_NOT_FOUND"


async def test_model_load_failure_still_ready(clean_db, data_dir):
    # Review Focus 5
    def broken():
        raise FileNotFoundError("chưa tải model")

    app = create_app(translator_factory=broken)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        view = await upload(client, THREE)
    assert view["status"] == "ready"
    assert view["chapters"][0]["title_vi"] == "Chương 1"
    assert "TITLE_UNTRANSLATED" in view["chapters"][0]["warnings"]


async def test_stale_analysis_does_not_overwrite_newer(api, fake_translator):
    # Hai lần phân tích chạy chồng: lần cũ (token cũ) không được ghi đè kết quả lần mới
    view = await upload(api, THREE)
    import_id = uuid.UUID(view["import_id"])
    await _set(view["import_id"], analysis_token=uuid.uuid4(), status="parsing")
    stale = uuid.uuid4()
    await imports.run_analysis(import_id, stale, lambda: fake_translator)
    async with get_sessionmaker()() as s:
        imp = await s.get(ImportSession, import_id)
    assert imp.status == "parsing" and len(imp.chapters) == 3
    base = imports.import_dir(import_id)
    assert (base / "chapters" / "c0001.txt").exists()
    assert not (base / f"chapters-{stale}").exists()


async def test_analysis_session_deleted_cleans_temp_dir(api, fake_translator):
    view = await upload(api, THREE)
    import_id = uuid.UUID(view["import_id"])
    token = uuid.uuid4()
    await _set(view["import_id"], analysis_token=token, status="parsing")
    async with get_sessionmaker()() as s:
        await s.execute(delete(ImportSession).where(ImportSession.id == import_id))
        await s.commit()
    await imports.run_analysis(import_id, token, lambda: fake_translator)
    assert not (imports.import_dir(import_id) / f"chapters-{token}").exists()


async def test_split_rule_change_clears_positional_edits(api):
    view = await upload(api, THREE)
    url = f"/api/v1/imports/{view['import_id']}"
    await api.patch(url, json={"chapters": [{"key": "c0003", "title_vi": "Tên tự sửa"}, {"key": "c0001", "selected": False}]})
    r = await api.patch(url, json={"split_rule": "blank_lines"})
    assert r.status_code == 200
    after = await wait_ready(api, view["import_id"])
    assert after["status"] == "ready" and after["split_rule"] == "blank_lines"
    assert all(c["selected"] and not c["title_vi_edited"] for c in after["chapters"])


async def test_unknown_chapter_key_rejected(api):
    view = await upload(api, THREE)
    r = await api.patch(f"/api/v1/imports/{view['import_id']}", json={"chapters": [{"key": "c9999", "selected": False}]})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "UNKNOWN_CHAPTER_KEY" and err["details"]["keys"] == ["c9999"]


async def test_failed_upload_leaves_no_orphan_dir(api, data_dir, monkeypatch):
    real = shutil.copyfileobj
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("đầy ổ đĩa")
        return real(*args, **kwargs)

    monkeypatch.setattr(imports.shutil, "copyfileobj", flaky)
    files = [("files[]", (f"{i}.txt", chapter("第1章 甲"), "text/plain")) for i in range(2)]
    with pytest.raises(OSError):
        await api.post("/api/v1/imports", data={"mode": "multi"}, files=files)
    assert not imports.imports_root().exists() or list(imports.imports_root().iterdir()) == []
