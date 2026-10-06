"""segments.dst_mt, segments.dst_ai: bản HachimiMT và bản DeepSeek mới nhất của từng câu (spec 06 mục 4a)

Backfill:
- Cột của engine hiện tại (theo chapters.model_id) = dst_machine.
- Cột của engine kia lấy từ snapshot của revision dịch mới nhất của engine đó (kind = machine, note IS NULL),
  ghép theo (idx, src). Revision soát (note = 'review') mang model DeepSeek nhưng nội dung là bản HachimiMT, nên bị bỏ.
- Chỉ ghi vào ô đang NULL: chạy lại không ghi đè.

Revision ID: 0011
Revises: 0010
"""
import logging

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

_DS = "deepseek%"  # cùng quy tắc với app.deepseek.catalog.is_deepseek_model


def _current(column: str, ai: bool) -> sa.TextClause:
    like = "LIKE" if ai else "NOT LIKE"
    return sa.text(f"""
        UPDATE segments s SET {column} = s.dst_machine
        FROM chapters c
        WHERE c.id = s.chapter_id AND NOT s.is_meta AND s.dst_machine IS NOT NULL AND s.{column} IS NULL
          AND coalesce(c.model_id, '') {like} :ds""")


def _other(column: str, ai: bool) -> sa.TextClause:
    rev_like, ch_like = ("LIKE", "NOT LIKE") if ai else ("NOT LIKE", "LIKE")
    return sa.text(f"""
        WITH latest AS (
            SELECT DISTINCT ON (r.chapter_id) r.chapter_id, r.snapshot
            FROM chapter_revisions r JOIN chapters c ON c.id = r.chapter_id
            WHERE r.kind = 'machine' AND r.note IS NULL AND jsonb_typeof(r.snapshot) = 'array'
              AND coalesce(r.model_id, '') {rev_like} :ds AND coalesce(c.model_id, '') {ch_like} :ds
            ORDER BY r.chapter_id, r.created_at DESC, r.id DESC
        ), items AS (
            SELECT l.chapter_id, (e ->> 'idx')::int AS idx, e ->> 'src' AS src, e ->> 'dst_machine' AS dst
            FROM latest l CROSS JOIN LATERAL jsonb_array_elements(l.snapshot) AS e
            WHERE jsonb_typeof(e) = 'object' AND e ->> 'idx' IS NOT NULL AND e ->> 'dst_machine' IS NOT NULL
        )
        UPDATE segments s SET {column} = i.dst
        FROM items i
        WHERE s.chapter_id = i.chapter_id AND s.idx = i.idx AND s.src = i.src AND NOT s.is_meta AND s.{column} IS NULL""")


def backfill(conn) -> dict[str, int]:
    p = {"ds": _DS}
    return {
        "current_mt": conn.execute(_current("dst_mt", ai=False), p).rowcount,
        "current_ai": conn.execute(_current("dst_ai", ai=True), p).rowcount,
        "other_mt": conn.execute(_other("dst_mt", ai=False), p).rowcount,
        "other_ai": conn.execute(_other("dst_ai", ai=True), p).rowcount,
    }


def upgrade() -> None:
    op.add_column("segments", sa.Column("dst_mt", sa.Text))
    op.add_column("segments", sa.Column("dst_ai", sa.Text))
    log.info("Bản theo engine: %s", backfill(op.get_bind()))


def downgrade() -> None:
    op.drop_column("segments", "dst_ai")
    op.drop_column("segments", "dst_mt")
