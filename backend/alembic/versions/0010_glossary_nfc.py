"""chuẩn hoá NFC cho glossary_terms (src_zh, dst_vi, aliases) và glossary_suggestions (src_zh, dst_vi);
bảng app_meta (khoá-giá trị, dùng cho dấu "đã nạp seed Hán Việt")

Revision ID: 0010
Revises: 0009
"""
import json
import logging
import unicodedata

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _normalize_table(conn, table: str, *, with_aliases: bool) -> int:
    """Chuẩn hoá từng dòng theo thứ tự (created_at, id). Nếu src_zh NFC trùng với một dòng khác cùng truyện
    (unique book_id + src_zh) thì dòng đến trước được giữ, dòng sau để nguyên (không xoá dữ liệu) và được ghi log."""
    cols = "id, book_id, src_zh, dst_vi" + (", aliases" if with_aliases else "")
    rows = conn.execute(sa.text(f"SELECT {cols} FROM {table} ORDER BY created_at, id")).mappings().all()
    taken = {(r["book_id"], r["src_zh"]) for r in rows}  # khoá đang tồn tại sau mọi thay đổi
    changed = 0
    for r in rows:
        src, dst = _nfc(r["src_zh"]), _nfc(r["dst_vi"])
        aliases = None
        if with_aliases:
            aliases = list(dict.fromkeys(_nfc(a) for a in (r["aliases"] or [])))
        if src != r["src_zh"]:
            if (r["book_id"], src) in taken:
                log.warning("%s: bỏ qua chuẩn hoá src_zh của %s (truyện %s) vì NFC trùng với dòng khác; giữ dòng đầu",
                            table, r["id"], r["book_id"])
                src = r["src_zh"]
            else:
                taken.discard((r["book_id"], r["src_zh"]))
                taken.add((r["book_id"], src))
        new = {"src": src, "dst": dst}
        if src == r["src_zh"] and dst == r["dst_vi"] and (not with_aliases or aliases == list(r["aliases"] or [])):
            continue
        sets = "src_zh = :src, dst_vi = :dst" + (", aliases = CAST(:aliases AS jsonb)" if with_aliases else "")
        if with_aliases:
            new["aliases"] = json.dumps(aliases, ensure_ascii=False)
        conn.execute(sa.text(f"UPDATE {table} SET {sets} WHERE id = :id"), {**new, "id": r["id"]})
        changed += 1
    return changed


def normalize_glossary(conn) -> dict[str, int]:
    return {"glossary_terms": _normalize_table(conn, "glossary_terms", with_aliases=True),
            "glossary_suggestions": _normalize_table(conn, "glossary_suggestions", with_aliases=False)}


def upgrade() -> None:
    op.create_table(
        "app_meta",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    n = normalize_glossary(op.get_bind())
    log.info("NFC glossary: %s", n)


def downgrade() -> None:
    op.drop_table("app_meta")  # dữ liệu glossary đã chuẩn hoá không khôi phục được dạng cũ
