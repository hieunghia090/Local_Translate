import zipfile
from datetime import datetime

from ebooklib import epub

from app.core.export import (
    ExportChapter,
    ExportOptions,
    SegmentText,
    bilingual_lines,
    chapter_lines,
    export_file_name,
    render_txt,
    reserve_path,
    write_epub,
    write_zip,
)


def chapter(no: int = 1, title_vi: str | None = "Chương 1: Mở đầu", body_dst: str = "Hắn đi rồi.") -> ExportChapter:
    heading = f"第{no}章 开始（求收藏）"
    return ExportChapter(no=no, title_zh=heading, title_vi=title_vi, segments=(
        SegmentText(heading, f"Chương {no} bắt đầu (cầu cất giữ)", False),
        SegmentText("=====", "=====", True),
        SegmentText("Nguồn: https://x.vn/1", "Nguồn: https://x.vn/1", True),
        SegmentText("", "", True),
        SegmentText("他走了。", body_dst, False),
        SegmentText("", "", True),
        SegmentText("", "", True),
        SegmentText("她笑了。", "Nàng cười.", False),
    ))


def test_title_inserted_once_and_meta_dropped():
    # Review Focus 1: không lặp tiêu đề (title_vi + bản dịch máy của dòng tiêu đề gốc)
    assert chapter_lines(chapter(), ExportOptions()) == ["Chương 1: Mở đầu", "", "Hắn đi rồi.", "", "Nàng cười."]


def test_keep_meta_keeps_text_meta_lines():
    assert chapter_lines(chapter(), ExportOptions(keep_meta=True)) == [
        "Chương 1: Mở đầu", "", "=====", "Nguồn: https://x.vn/1", "", "Hắn đi rồi.", "", "Nàng cười.",
    ]


def test_without_titles_keeps_machine_heading():
    # Review Focus 1: tắt "Chèn tiêu đề" thì giữ dòng tiêu đề dịch máy, không mất tiêu đề
    assert chapter_lines(chapter(), ExportOptions(include_titles=False)) == [
        "Chương 1 bắt đầu (cầu cất giữ)", "", "Hắn đi rồi.", "", "Nàng cười.",
    ]


def test_title_falls_back_to_title_zh():
    assert chapter_lines(chapter(title_vi=None), ExportOptions())[0] == "第1章 开始（求收藏）"


def test_render_txt_joins_chapters_in_given_order():
    text = render_txt([chapter(1), chapter(2, "Chương 2: Tiếp")], ExportOptions())
    assert text == (
        "Chương 1: Mở đầu\n\nHắn đi rồi.\n\nNàng cười.\n\n\n"
        "Chương 2: Tiếp\n\nHắn đi rồi.\n\nNàng cười.\n"
    )


def test_untranslated_sentence_is_blank_not_none():
    assert "None" not in render_txt([chapter(body_dst=None)], ExportOptions())


def test_bilingual_source_then_translation():
    assert bilingual_lines(chapter(), ExportOptions()) == [
        "第1章 开始（求收藏）", "Chương 1: Mở đầu", "", "他走了。", "Hắn đi rồi.", "", "她笑了。", "Nàng cười.",
    ]


def test_zip_one_file_per_chapter(tmp_path):
    path = tmp_path / "out.zip"
    write_zip(path, [chapter(1), chapter(2, "Chương 2: Tiếp")], ExportOptions())
    with zipfile.ZipFile(path) as zf:
        assert zf.namelist() == ["0001.txt", "0002.txt"]
        assert zf.read("0002.txt").decode() == "Chương 2: Tiếp\n\nHắn đi rồi.\n\nNàng cười.\n"


def test_epub_toc_metadata_and_escaping(tmp_path):
    # BR-3.17; Review Focus 2
    first = chapter(1, "Chương 1: <Kiếm> & Đao", body_dst="Hắn nói: <b>chạy</b> & \x0bđi")
    path = tmp_path / "out.epub"
    write_epub(path, [first, chapter(2, "Chương 2: Tiếp")], ExportOptions(),
               identifier="book-1", title="Đại Tống", author="Tác giả")
    book = epub.read_epub(str(path))
    assert [link.title for link in book.toc] == ["Chương 1: <Kiếm> & Đao", "Chương 2: Tiếp"]
    assert book.get_metadata("DC", "language")[0][0] == "vi"
    assert book.get_metadata("DC", "title")[0][0] == "Đại Tống"
    assert [m[0] for m in book.get_metadata("DC", "creator")] == ["Tác giả"]
    html = book.get_item_with_href("chap_0001.xhtml").get_content().decode("utf-8")
    assert "&lt;b&gt;chạy&lt;/b&gt; &amp; đi" in html
    assert "\x0b" not in html


def test_file_name_and_reserve(tmp_path):
    # BR-3.16; Review Focus 4
    now = datetime(2026, 10, 4, 9, 5)
    assert export_file_name("dai-tong", "reviewed", "txt", now) == "dai-tong_da-soat_20261004-0905.txt"
    assert export_file_name("dai-tong", "translated", "epub", now) == "dai-tong_da-dich_20261004-0905.epub"
    assert export_file_name("dai-tong", "range", "bilingual", now, 3, 9) == "dai-tong_chuong-3-9-song-ngu_20261004-0905.txt"
    assert export_file_name("dai-tong", "range", "zip", now, 3, 9) == "dai-tong_chuong-3-9_20261004-0905.zip"

    folder = tmp_path / "exports"
    final, part = reserve_path(folder, "a_da-dich_20261004-0905.txt")
    assert final.name == "a_da-dich_20261004-0905.txt" and part.name == final.name + ".part" and part.exists()
    final.write_text("xong")
    part.unlink()
    second, part2 = reserve_path(folder, "a_da-dich_20261004-0905.txt")
    assert second.name == "a_da-dich_20261004-0905-2.txt"
    third, _ = reserve_path(folder, "a_da-dich_20261004-0905.txt")  # -2 đang giữ chỗ bằng .part
    assert third.name == "a_da-dich_20261004-0905-3.txt"
