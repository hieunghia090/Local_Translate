"""Dựng nội dung file xuất bản dịch (spec 03 mục 7). Hàm thuần, không đụng DB."""
import html
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

FORMAT_EXT = {"txt": "txt", "bilingual": "txt", "zip": "zip", "epub": "epub"}
MEDIA_TYPES = {
    "txt": "text/plain; charset=utf-8",
    "bilingual": "text/plain; charset=utf-8",
    "zip": "application/zip",
    "epub": "application/epub+zip",
}
_SCOPE_LABEL = {"translated": "da-dich", "reviewed": "da-soat"}
TITLE_MAX = 200  # splitter (G2) lấy title_zh = dòng không rỗng đầu tiên, cắt ở 200 ký tự
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True)
class SegmentText:
    src: str
    dst: str | None
    is_meta: bool


@dataclass(frozen=True)
class ExportChapter:
    no: int
    title_zh: str
    title_vi: str | None
    segments: tuple[SegmentText, ...]


@dataclass(frozen=True)
class ExportOptions:
    include_titles: bool = True
    keep_meta: bool = False


def chapter_title(ch: ExportChapter) -> str:
    return (ch.title_vi or "").strip() or ch.title_zh.strip() or f"Chương {ch.no}"


def _body_segments(ch: ExportChapter, opts: ExportOptions) -> list[SegmentText]:
    out: list[SegmentText] = []
    first = True
    for seg in ch.segments:
        text = seg.src.strip()
        if first and text:
            first = False
            # Đã chèn tiêu đề Việt thì bỏ bản dịch máy của dòng tiêu đề gốc (không lặp tiêu đề).
            if opts.include_titles and text[:TITLE_MAX] == ch.title_zh.strip():
                continue
        if seg.is_meta and text and not opts.keep_meta:
            continue
        out.append(seg)
    return out


def _tidy(lines: list[str]) -> list[str]:
    """Gộp các dòng trống liền nhau thành một, bỏ dòng trống ở đầu và ở cuối."""
    out: list[str] = []
    for line in lines:
        if line:
            out.append(line)
        elif out and out[-1]:
            out.append("")
    while out and not out[-1]:
        out.pop()
    return out


def body_lines(ch: ExportChapter, opts: ExportOptions) -> list[str]:
    lines = [seg.src.strip() if seg.is_meta else (seg.dst or "").strip() for seg in _body_segments(ch, opts)]
    return _tidy(lines)


def chapter_lines(ch: ExportChapter, opts: ExportOptions) -> list[str]:
    body = body_lines(ch, opts)
    return [chapter_title(ch), "", *body] if opts.include_titles else body


def render_txt(chapters: Sequence[ExportChapter], opts: ExportOptions) -> str:
    return "\n\n\n".join("\n".join(chapter_lines(ch, opts)) for ch in chapters) + "\n"


def bilingual_lines(ch: ExportChapter, opts: ExportOptions) -> list[str]:
    """Câu gốc rồi câu dịch, mỗi cặp cách nhau một dòng trống."""
    lines = [ch.title_zh.strip(), chapter_title(ch), ""] if opts.include_titles else []
    for seg in _body_segments(ch, opts):
        src = seg.src.strip()
        if not src:
            continue
        lines += [src, ""] if seg.is_meta else [src, (seg.dst or "").strip(), ""]
    return _tidy(lines)


def render_bilingual(chapters: Sequence[ExportChapter], opts: ExportOptions) -> str:
    return "\n\n\n".join("\n".join(bilingual_lines(ch, opts)) for ch in chapters) + "\n"


def write_zip(path: Path, chapters: Sequence[ExportChapter], opts: ExportOptions) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for ch in chapters:
            zf.writestr(f"{ch.no:04d}.txt", "\n".join(chapter_lines(ch, opts)) + "\n")


def _xml(text: str) -> str:
    return _XML_BAD.sub("", text)


def write_epub(path: Path, chapters: Sequence[ExportChapter], opts: ExportOptions, *,
               identifier: str, title: str, author: str | None) -> None:
    """BR-3.17: mục lục theo title_vi, metadata tên / tác giả / ngôn ngữ vi."""
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier(identifier)
    book.set_title(_xml(title))
    book.set_language("vi")
    if author:
        book.add_author(_xml(author))
    items = []
    for ch in chapters:
        name = _xml(chapter_title(ch))
        parts = [f"<h1>{html.escape(name)}</h1>"] if opts.include_titles else []
        parts += [f"<p>{html.escape(_xml(line))}</p>" for line in body_lines(ch, opts) if line]
        item = epub.EpubHtml(title=name, file_name=f"chap_{ch.no:04d}.xhtml", lang="vi")
        item.content = "".join(parts) or "<p></p>"
        book.add_item(item)
        items.append(item)
    book.toc = items
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", *items]
    epub.write_epub(str(path), book)


def write_export(path: Path, fmt: str, chapters: Sequence[ExportChapter], opts: ExportOptions, *,
                 identifier: str, title: str, author: str | None) -> None:
    if fmt == "txt":
        path.write_text(render_txt(chapters, opts), encoding="utf-8")
    elif fmt == "bilingual":
        path.write_text(render_bilingual(chapters, opts), encoding="utf-8")
    elif fmt == "zip":
        write_zip(path, chapters, opts)
    elif fmt == "epub":
        write_epub(path, chapters, opts, identifier=identifier, title=title, author=author)
    else:
        raise ValueError(f"Định dạng không hỗ trợ: {fmt}")


def export_file_name(slug: str, scope: str, fmt: str, now: datetime,
                     from_no: int | None = None, to_no: int | None = None) -> str:
    """BR-3.16: <slug>_<phạm vi>_<yyyyMMdd-HHmm>.<ext>"""
    part = _SCOPE_LABEL.get(scope) or f"chuong-{from_no}-{to_no}"
    if fmt == "bilingual":
        part += "-song-ngu"
    return f"{slug}_{part}_{now:%Y%m%d-%H%M}.{FORMAT_EXT[fmt]}"


def reserve_path(folder: Path, name: str) -> tuple[Path, Path]:
    """Chọn tên chưa dùng (thêm -2, -3… trước đuôi) và giữ chỗ bằng file .part tạo độc quyền."""
    folder.mkdir(parents=False, exist_ok=True)  # không tạo lại thư mục truyện đã bị xoá
    stem, _, ext = name.rpartition(".")
    n = 1
    while True:
        final = folder / (name if n == 1 else f"{stem}-{n}.{ext}")
        part = final.with_name(final.name + ".part")
        if not final.exists():
            try:
                part.open("x").close()
                return final, part
            except FileExistsError:
                pass
        n += 1
