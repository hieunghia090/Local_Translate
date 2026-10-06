import re
from collections.abc import Callable
from dataclasses import dataclass

from app.core.lines import count_han, has_chinese
from app.core.titles import is_author_note, split_heading

AUTO_HEADING = re.compile(r"^(第[0-9零一二三四五六七八九十百千两〇]+[章回节卷].*|Chương\s*\d+.*|#{1,3}\s+.+)$")
AUTHOR_NOTE_HEADING = re.compile(r"^(作者的话|作者有话说|[^。！？，]{0,10}感言|请假条?)[：:]?$")
MAX_HEADING_CHARS = 50
SHORT_CHAPTER_CHARS = 200
_VOLUME = re.compile(r"^第[0-9零一二三四五六七八九十百千两〇]+卷")
_BLANK_SPLIT = re.compile(r"\n[ \t　]*\n(?:[ \t　]*\n)+")
_LEADING_NUMBER = re.compile(r"^\s*(\d+)")


@dataclass(frozen=True)
class DraftChapter:
    title_zh: str
    text: str
    chars: int
    is_prologue: bool = False


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.split("\n") if line.strip()), "")


def _draft(lines: list[str], *, is_prologue: bool = False) -> DraftChapter:
    text = "\n".join(lines).strip("\n")
    return DraftChapter(_first_line(text)[:200], text, count_han(text), is_prologue)


def _auto_heading_detector() -> Callable[[str], bool]:
    last_no: int | None = None

    def is_heading(line: str) -> bool:
        nonlocal last_no
        if len(line) > MAX_HEADING_CHARS or "。" in line:  # câu văn bắt đầu bằng 第一回合… không phải tiêu đề
            return False
        if AUTHOR_NOTE_HEADING.match(line):
            return True
        if not AUTO_HEADING.match(line):
            return False
        if _VOLUME.match(line):  # sang quyển mới, số chương có thể đếm lại từ 1
            last_no = None
            return True
        no = split_heading(line)[0]
        if no is not None and last_no is not None and no <= last_no:
            return False  # tiêu đề lặp lại trong thân chương, hoặc câu văn kiểu 第二回是…
        if no is not None:
            last_no = no
        return True

    return is_heading


def _split_on(text: str, is_heading: Callable[[str], bool]) -> list[DraftChapter]:
    prologue: list[str] = []
    chapters: list[list[str]] = []
    for line in text.split("\n"):
        t = line.strip()
        if t and is_heading(t):
            chapters.append([line])
        elif chapters:
            chapters[-1].append(line)
        else:
            prologue.append(line)
    if not chapters:
        return [_draft(prologue)] if has_chinese(text) else []
    drafts = [_draft(lines) for lines in chapters]
    if has_chinese("\n".join(prologue)):
        drafts.insert(0, _draft(prologue, is_prologue=True))
    return drafts


def split_single(text: str, rule: str = "auto", regex: str | None = None) -> list[DraftChapter]:
    if rule == "auto":
        return _split_on(text, _auto_heading_detector())
    if rule == "regex":
        rx = re.compile(regex or "")
        return _split_on(text, lambda t: bool(rx.search(t)))
    if rule == "blank_lines":
        return [_draft(block.split("\n")) for block in _BLANK_SPLIT.split(text) if has_chinese(block)]
    raise ValueError(f"Quy tắc tách chương không hợp lệ: {rule!r}")


def _basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def order_files(names: list[str]) -> list[int]:
    """Thứ tự file theo số đầu tên file (0016 - … → 16). File không có số xếp sau, theo tên (BR-2.4)."""

    def key(i: int) -> tuple[int, int, str]:
        base = _basename(names[i])
        m = _LEADING_NUMBER.match(base)
        return (0, int(m.group(1)), base) if m else (1, 0, base)

    return sorted(range(len(names)), key=key)


def chapter_from_file(name: str, text: str) -> DraftChapter:
    draft = _draft(text.split("\n"))
    if draft.title_zh:
        return draft
    return DraftChapter(_basename(name).rsplit(".", 1)[0], draft.text, draft.chars)


def default_selection(d: DraftChapter) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    if d.chars < SHORT_CHAPTER_CHARS:
        warnings.append("SHORT")
    if is_author_note(d.title_zh):
        warnings.append("AUTHOR_NOTE")
    return (True if d.is_prologue else not warnings), warnings
