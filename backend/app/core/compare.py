"""Bản HachimiMT và bản DeepSeek của một câu (spec 06 mục 4a, BR-6.10, BR-6.11). Hàm thuần, không đụng DB."""
import re
import unicodedata

from app.deepseek.catalog import is_deepseek_model

# Dùng chung cho Python và SQL (services/versions.py): số câu khác nhau ở danh sách chương phải bằng ở trang Dịch chương.
WS_CHARS = " \t\n\r\f\v 　"
WS_PATTERN = f"[{WS_CHARS}]+"
_WS = re.compile(WS_PATTERN)
MT_COLUMN = "dst_mt"
AI_COLUMN = "dst_ai"


def norm_cmp(text: str) -> str:
    """NFC, gộp khoảng trắng thành một dấu cách, bỏ dấu cách hai đầu (BR-6.11)."""
    return _WS.sub(" ", unicodedata.normalize("NFC", text)).strip(" ")


def versions_differ(mt: str | None, ai: str | None) -> bool | None:
    if mt is None or ai is None:
        return None
    return norm_cmp(mt) != norm_cmp(ai)


def engine_column(model_id: str | None) -> str:
    """Cột của engine đã dịch ra `dst_machine` hiện tại. Model không phải DeepSeek (kể cả None, "fake") là HachimiMT."""
    return AI_COLUMN if is_deepseek_model(model_id) else MT_COLUMN


def carried_versions(column: str, src: str, machine: str | None,
                     prev: tuple[str, str | None, str | None] | None) -> dict[str, str | None]:
    """Giá trị dst_mt / dst_ai cho một câu vừa dịch: cột của engine vừa chạy = `machine`; cột kia lấy từ dòng cũ
    cùng idx nếu `src` không đổi (BR-6.10). `prev` = (src, dst_mt, dst_ai) của dòng cũ."""
    other = AI_COLUMN if column == MT_COLUMN else MT_COLUMN
    kept = None
    if prev is not None and prev[0] == src:
        kept = prev[1] if other == MT_COLUMN else prev[2]
    return {column: machine, other: kept}
