import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.core.lines import has_chinese
from app.core.splitter import DraftChapter, chapter_from_file, default_selection, order_files, split_single
from app.core.textio import SourceDecodeError, UnsupportedEncodingError, decode_source
from app.core.titles import default_title_vi, split_heading, strip_ads, translation_source
from app.core.translator import Translator

log = logging.getLogger(__name__)

MODES = ("single", "multi")
SPLIT_RULES = ("auto", "blank_lines", "regex")
ENCODINGS = ("auto", "utf-8", "gbk", "big5")
SUPPORTED_SUFFIXES = (".txt", ".md")
PROLOGUE_TITLE_VI = "Mở đầu"
TITLE_BATCH = 64
_ID_SUFFIX = re.compile(r"\s*-{1,2}\s*\d+\s*$")


@dataclass(frozen=True)
class StoredFile:
    name: str  # tên gốc lúc upload, chỉ để hiển thị
    path: str | None  # None khi file vượt giới hạn dung lượng và không được lưu
    size: int


@dataclass(frozen=True)
class AnalyzedChapter:
    key: str
    no: int
    title_zh: str
    title_machine: str | None
    title_number: int | None
    chars: int
    selected: bool
    warnings: list[str]
    is_prologue: bool
    text: str = field(repr=False)

    def to_json(self) -> dict:
        data = asdict(self)
        data.pop("text")
        return data


@dataclass(frozen=True)
class Analysis:
    chapters: list[AnalyzedChapter]
    file_errors: list[dict]
    total_chars: int
    suggested_title_zh: str | None


def default_title(ch: dict, no: int | None = None) -> str:
    """Tiêu đề Việt mặc định (BR-2.6). `no` là số thứ tự cuối cùng khi tạo truyện."""
    if ch["is_prologue"]:
        return PROLOGUE_TITLE_VI
    return default_title_vi(ch["title_number"], no or ch["no"], ch["title_machine"])


def _common_dir(names: list[str]) -> str | None:
    parts = [n.replace("\\", "/").split("/") for n in names]
    if parts and all(len(p) > 1 for p in parts) and len({p[0] for p in parts}) == 1:
        return parts[0][0]
    return None


def suggest_title(names: list[str], source_name: str | None, mode: str) -> str | None:
    """'大宋有种--35466' → '大宋有种'. Lấy từ tên thư mục, hoặc tên file khi nhập một file."""
    candidate = source_name or _common_dir(names)
    if not candidate and mode == "single" and len(names) == 1:
        candidate = Path(names[0].replace("\\", "/")).stem
    if not candidate:
        return None
    return _ID_SUFFIX.sub("", candidate.strip()).strip() or None


def _file_error(f: StoredFile) -> dict | None:
    if Path(f.name).suffix.lower() not in SUPPORTED_SUFFIXES:
        return {"file": f.name, "code": "UNSUPPORTED_TYPE", "message": "Chỉ nhận file .txt hoặc .md"}
    if f.path is None:
        return {"file": f.name, "code": "FILE_TOO_LARGE", "message": "File lớn hơn 50 MB"}
    return None


def _decode_all(files: list[StoredFile], encoding: str) -> tuple[list[tuple[str, str]], list[dict]]:
    decoded: list[tuple[str, str]] = []
    errors: list[dict] = []
    for f in files:
        if err := _file_error(f):
            errors.append(err)
            continue
        try:
            decoded.append((f.name, decode_source(Path(f.path).read_bytes(), encoding=encoding).text))
        except (SourceDecodeError, UnsupportedEncodingError) as e:
            errors.append({"file": f.name, "code": "ENCODING", "message": f"{e}. Thử chọn encoding khác."})
    return decoded, errors


def _translate_titles(drafts: list[DraftChapter], translator: Translator | None) -> tuple[list[str | None], bool]:
    """Trả (bản dịch từng tiêu đề, có lỗi dịch không). Mỗi tiêu đề khác nhau chỉ dịch một lần."""
    sources = [None if d.is_prologue else translation_source(d.title_zh) for d in drafts]
    pending = list(dict.fromkeys(s for s in sources if s and has_chinese(s)))
    done: dict[str, str] = {}
    failed = False
    if pending:
        try:
            if translator is None:
                raise RuntimeError("Chưa có model dịch")
            batch = translator.translate(pending, beam=1, batch_size=TITLE_BATCH)
            if len(batch.outputs) != len(pending):
                raise RuntimeError("Model trả thiếu tiêu đề")
            done = {s: o.strip() for s, o in zip(pending, batch.outputs)}
        except Exception:  # thiếu model, lỗi nạp model…: vẫn cho xem trước, chỉ thiếu bản dịch tiêu đề
            log.warning("Không dịch được tiêu đề chương", exc_info=True)
            failed = True

    def machine(s: str | None) -> str | None:
        if not s:
            return None
        return (done.get(s) or None) if has_chinese(s) else s

    return [machine(s) for s in sources], failed


def analyze(
    files: list[StoredFile],
    *,
    mode: str,
    split_rule: str = "auto",
    split_regex: str | None = None,
    encoding: str = "auto",
    translator: Translator | None = None,
    source_name: str | None = None,
) -> Analysis:
    decoded, errors = _decode_all(files, encoding)
    if mode == "single":
        drafts = split_single(decoded[0][1], split_rule, split_regex) if decoded else []
    else:
        drafts = [chapter_from_file(*decoded[i]) for i in order_files([n for n, _ in decoded])]

    machine, failed = _translate_titles(drafts, translator)
    chapters: list[AnalyzedChapter] = []
    for no, (d, m) in enumerate(zip(drafts, machine), start=1):
        selected, warnings = default_selection(d)
        if failed and not d.is_prologue and has_chinese(translation_source(d.title_zh)):
            warnings = [*warnings, "TITLE_UNTRANSLATED"]
        number = None if d.is_prologue else split_heading(strip_ads(d.title_zh))[0]
        chapters.append(
            AnalyzedChapter(f"c{no:04d}", no, d.title_zh, m, number, d.chars, selected, warnings, d.is_prologue, d.text)
        )
    return Analysis(
        chapters,
        errors,
        sum(c.chars for c in chapters),
        suggest_title([f.name for f in files], source_name, mode),
    )
