"""exports (G10), index trigram tìm chương theo tên

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0008"
down_revision = "0007"  # 0007 là migration của G8 (DeepSeek). G10 làm sau khi G8 đã merge.
branch_labels = None
depends_on = None

# Spec 00 mục 8: tìm không dấu bằng pg_trgm. f_unaccent có từ 0002.
CHAPTER_SEARCH = {
    "title_vi": "f_unaccent(lower(coalesce(title_vi, '')))",
    "title_zh": "title_zh",
}


def upgrade() -> None:
    op.create_table(
        "exports",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", pg.UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.Text, nullable=False),
        sa.Column("format", sa.Text, nullable=False),
        sa.Column("from_no", sa.Integer),
        sa.Column("to_no", sa.Integer),
        sa.Column("include_titles", sa.Boolean, nullable=False),
        sa.Column("keep_meta", sa.Boolean, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="running"),
        sa.Column("file_name", sa.Text),
        sa.Column("chapters", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped", sa.Integer, nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("scope IN ('translated', 'reviewed', 'range')", name="ck_exports_scope"),
        sa.CheckConstraint("format IN ('txt', 'zip', 'epub', 'bilingual')", name="ck_exports_format"),
        sa.CheckConstraint("status IN ('running', 'done', 'failed')", name="ck_exports_status"),
    )
    op.create_index("ix_exports_book_id", "exports", ["book_id"])
    for name, expr in CHAPTER_SEARCH.items():
        op.execute(f"CREATE INDEX ix_chapters_search_{name} ON chapters USING gin (({expr}) gin_trgm_ops)")


def downgrade() -> None:
    for name in CHAPTER_SEARCH:
        op.execute(f"DROP INDEX IF EXISTS ix_chapters_search_{name}")
    op.drop_table("exports")
