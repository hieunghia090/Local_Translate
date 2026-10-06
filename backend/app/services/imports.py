import logging
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anyio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.config import get_settings
from app.core.translator import Translator
from app.db import get_sessionmaker
from app.errors import AppError
from app.ids import uuid7
from app.models import ImportSession
from app.services.import_analysis import ENCODINGS, MODES, SPLIT_RULES, Analysis, StoredFile, analyze, default_title

log = logging.getLogger(__name__)

MAX_FILES = 5000
MAX_FILE_BYTES = 50 * 1024 * 1024
TTL = timedelta(hours=24)
STALE_PARSING = timedelta(minutes=10)
_COPY_CHUNK = 1024 * 1024
_REPARSE_FIELDS = ("split_rule", "split_regex", "encoding")


def imports_root() -> Path:
    return get_settings().data_path / "imports"


def import_dir(import_id: uuid.UUID) -> Path:
    return imports_root() / str(import_id)


def chapter_text_path(import_id: uuid.UUID, key: str) -> Path:
    return import_dir(import_id) / "chapters" / f"{key}.txt"


def validate_params(mode: str, split_rule: str, split_regex: str | None, encoding: str) -> None:
    if mode not in MODES:
        raise AppError("INVALID_MODE", "Chế độ nhập phải là single hoặc multi", 422)
    if split_rule not in SPLIT_RULES:
        raise AppError("INVALID_SPLIT_RULE", "Quy tắc tách chương phải là auto, blank_lines hoặc regex", 422)
    if split_rule == "regex":
        if not split_regex:
            raise AppError("INVALID_REGEX", "Cần nhập regex để tách chương", 422)
        try:
            re.compile(split_regex)
        except re.error as e:
            raise AppError("INVALID_REGEX", f"Regex không hợp lệ: {e}", 422) from e
    if encoding not in ENCODINGS:
        raise AppError("UNSUPPORTED_ENCODING", "Encoding phải là auto, utf-8, gbk hoặc big5", 422)


def _store_uploads(raw: Path, uploads: list[UploadFile]) -> list[dict]:
    """Ghi file upload vào raw/00000…; tên gốc chỉ được lưu làm dữ liệu, không dùng làm đường dẫn."""
    raw.mkdir(parents=True, exist_ok=True)
    stored: list[dict] = []
    try:
        for i, up in enumerate(uploads):
            name = up.filename or f"file-{i + 1}"
            dest = raw / f"{i:05d}"
            with dest.open("wb") as fh:
                shutil.copyfileobj(up.file, fh, _COPY_CHUNK)
            size = dest.stat().st_size
            if size > MAX_FILE_BYTES:
                dest.unlink()
                stored.append(asdict(StoredFile(name, None, size)))
            else:
                stored.append(asdict(StoredFile(name, f"raw/{dest.name}", size)))
    except BaseException:
        shutil.rmtree(raw.parent, ignore_errors=True)  # không để lại thư mục import mồ côi
        raise
    return stored


async def create_import(
    session: AsyncSession,
    *,
    mode: str,
    split_rule: str,
    split_regex: str | None,
    encoding: str,
    source_name: str | None,
    uploads: list[UploadFile],
) -> ImportSession:
    validate_params(mode, split_rule, split_regex, encoding)
    if not uploads:
        raise AppError("NO_FILES", "Chưa chọn file nào", 422)
    if len(uploads) > MAX_FILES:
        raise AppError("TOO_MANY_FILES", f"Tối đa {MAX_FILES} file mỗi lần", 422)
    if mode == "single" and len(uploads) != 1:
        raise AppError("SINGLE_MODE_ONE_FILE", "Chế độ một file chỉ nhận đúng 1 file", 422)

    import_id = uuid7()
    files = await anyio.to_thread.run_sync(_store_uploads, import_dir(import_id) / "raw", uploads)
    imp = ImportSession(
        id=import_id,
        status="parsing",
        mode=mode,
        split_rule=split_rule,
        split_regex=split_regex,
        encoding=encoding,
        source_name=source_name,
        files=files,
        chapters=[],
        file_errors=[],
        edits={},
        total_chars=0,
        expires_at=datetime.now(timezone.utc) + TTL,
        analysis_token=uuid7(),
    )
    session.add(imp)
    await session.commit()
    return imp


def _load_translator(factory: Callable[[], Translator]) -> Translator | None:
    try:
        return factory()
    except Exception:
        log.warning("Không nạp được model dịch tiêu đề", exc_info=True)
        return None


def _analyze_and_write(
    import_id: uuid.UUID, token: uuid.UUID, files: list[StoredFile], params: dict, factory: Callable[[], Translator]
) -> Analysis:
    result = analyze(files, translator=_load_translator(factory), **params)
    base = import_dir(import_id)
    if not base.exists():  # phiên đã bị xoá giữa chừng: đừng tạo lại thư mục mồ côi
        return result
    out = base / f"chapters-{token}"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir()
    for c in result.chapters:
        (out / f"{c.key}.txt").write_text(c.text, encoding="utf-8")
    return result


def _commit_chapters_dir(import_id: uuid.UUID, token: uuid.UUID) -> None:
    base = import_dir(import_id)
    shutil.rmtree(base / "chapters", ignore_errors=True)
    (base / f"chapters-{token}").rename(base / "chapters")


def _discard_chapters_dir(import_id: uuid.UUID, token: uuid.UUID) -> None:
    shutil.rmtree(import_dir(import_id) / f"chapters-{token}", ignore_errors=True)


async def run_analysis(
    import_id: uuid.UUID, token: uuid.UUID, translator_factory: Callable[[], Translator]
) -> None:
    """Chỉ lần phân tích mang token hiện tại trong DB mới được ghi kết quả; các lần cũ bị bỏ."""
    sessions = get_sessionmaker()
    try:
        async with sessions() as session:
            imp = await session.get(ImportSession, import_id)
            if imp is None:
                return
            base = import_dir(import_id)
            files = [StoredFile(f["name"], str(base / f["path"]) if f["path"] else None, f["size"]) for f in imp.files]
            params = {
                "mode": imp.mode,
                "split_rule": imp.split_rule,
                "split_regex": imp.split_regex,
                "encoding": imp.encoding,
                "source_name": imp.source_name,
            }
        result = await anyio.to_thread.run_sync(
            _analyze_and_write, import_id, token, files, params, translator_factory
        )
        values = {
            "status": "ready",
            "error": None,
            "chapters": [c.to_json() for c in result.chapters],
            "file_errors": result.file_errors,
            "total_chars": result.total_chars,
            "suggested_title_zh": result.suggested_title_zh,
        }
    except Exception as e:
        log.exception("Phân tích import %s lỗi", import_id)
        await anyio.to_thread.run_sync(_discard_chapters_dir, import_id, token)
        values = {"status": "failed", "error": f"Phân tích file thất bại: {e}"[:500]}
    async with sessions() as session:
        lock_stmt = (
            select(ImportSession)
            .where(
                ImportSession.id == import_id,
                ImportSession.analysis_token == token,
                ImportSession.status == "parsing",
            )
            .with_for_update()
        )
        imp = (await session.execute(lock_stmt.execution_options(populate_existing=True))).scalar_one_or_none()
        if imp is None:
            await session.rollback()
            await anyio.to_thread.run_sync(_discard_chapters_dir, import_id, token)
            return
        # Giữ khoá dòng trong lúc đổi thư mục chapters/ để create_book không thấy trạng thái nửa vời.
        if values["status"] == "ready":
            await anyio.to_thread.run_sync(_commit_chapters_dir, import_id, token)
        for k, v in values.items():
            setattr(imp, k, v)
        await session.commit()


async def get_import(session: AsyncSession, import_id: uuid.UUID, *, lock: bool = False) -> ImportSession:
    """`lock=True`: khoá dòng (FOR UPDATE) tới khi commit, để tạo truyện / sửa phiên không chạy song song."""
    if lock:
        stmt = select(ImportSession).where(ImportSession.id == import_id).with_for_update()
        imp = (await session.execute(stmt.execution_options(populate_existing=True))).scalar_one_or_none()
    else:
        imp = await session.get(ImportSession, import_id)
    now = datetime.now(timezone.utc)
    if imp is None or imp.expires_at < now:
        raise AppError("IMPORT_NOT_FOUND", "Không tìm thấy phiên nhập file (có thể đã hết hạn)", 404)
    if imp.status == "parsing" and imp.updated_at < now - STALE_PARSING:
        # server tắt giữa lúc phân tích: đừng để phiên kẹt mãi ở parsing
        imp.status = "failed"
        imp.error = "Phân tích bị gián đoạn. Hãy chọn lại tuỳ chọn để phân tích lại."
        await session.commit()
    return imp


def _row(ch: dict, edit: dict) -> dict:
    return {
        "key": ch["key"],
        "no": ch["no"],
        "title_zh": ch["title_zh"],
        "title_vi": edit.get("title_vi") or default_title(ch),
        "title_vi_edited": "title_vi" in edit,
        "chars": ch["chars"],
        "selected": edit.get("selected", ch["selected"]),
        "warnings": ch["warnings"],
    }


def to_view(imp: ImportSession) -> dict:
    return {
        "import_id": str(imp.id),
        "status": imp.status,
        "mode": imp.mode,
        "split_rule": imp.split_rule,
        "split_regex": imp.split_regex,
        "encoding": imp.encoding,
        "suggested_title_zh": imp.suggested_title_zh,
        "total_chars": imp.total_chars,
        "chapters": [_row(ch, imp.edits.get(ch["key"], {})) for ch in imp.chapters],
        "file_errors": imp.file_errors,
        "error": imp.error,
    }


def selected_chapters(imp: ImportSession) -> list[tuple[dict, str | None]]:
    out: list[tuple[dict, str | None]] = []
    for ch in imp.chapters:
        edit = imp.edits.get(ch["key"], {})
        if edit.get("selected", ch["selected"]):
            out.append((ch, edit.get("title_vi")))
    return out


async def patch_import(
    session: AsyncSession, imp: ImportSession, *, params: dict, chapters: list[dict]
) -> tuple[dict, bool]:
    """`params` chỉ chứa các trường người dùng gửi trong số split_rule / split_regex / encoding."""
    known = {ch["key"] for ch in imp.chapters}
    if known:
        unknown = sorted({e["key"] for e in chapters} - known)
        if unknown:
            raise AppError("UNKNOWN_CHAPTER_KEY", "Không có chương với key này", 422, {"keys": unknown})
    edits = dict(imp.edits)
    if any(k in params and params[k] != getattr(imp, k) for k in ("split_rule", "split_regex")):
        # đổi cách tách chương: vị trí/key không còn trỏ tới cùng chương nữa, bỏ các chỉnh sửa cũ
        edits = {}
    for e in chapters:
        cur = dict(edits.get(e["key"], {}))
        if e.get("selected") is not None:
            cur["selected"] = e["selected"]
        if e.get("title_vi") is not None:
            title = e["title_vi"].strip()
            if title:
                cur["title_vi"] = title
            else:
                cur.pop("title_vi", None)  # xoá trắng: quay về tiêu đề tự dịch
        edits[e["key"]] = cur
    imp.edits = edits

    new = {k: params.get(k, getattr(imp, k)) for k in _REPARSE_FIELDS}
    changed = any(new[k] != getattr(imp, k) for k in _REPARSE_FIELDS)
    reparse = bool(params) and (changed or imp.status == "failed")
    if reparse:
        if imp.status == "parsing":
            raise AppError("IMPORT_BUSY", "File đang được phân tích, thử lại sau giây lát", 409)
        validate_params(imp.mode, new["split_rule"], new["split_regex"], new["encoding"])
        imp.split_rule, imp.split_regex, imp.encoding = new["split_rule"], new["split_regex"], new["encoding"]
        imp.status, imp.error = "parsing", None
        imp.analysis_token = uuid7()
        imp.ai_preview = []  # đề xuất chạy thử thuộc lần phân tích cũ (Review Focus 5)
    await session.commit()
    return to_view(imp), reparse


async def purge_expired(session: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    ids = (
        await session.execute(delete(ImportSession).where(ImportSession.expires_at < now).returning(ImportSession.id))
    ).scalars().all()
    await session.commit()
    for import_id in ids:
        shutil.rmtree(import_dir(import_id), ignore_errors=True)
    return len(ids)
