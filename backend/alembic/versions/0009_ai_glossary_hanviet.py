"""ai glossary + hán việt: glossary_suggestions, hanviet_readings, hanviet_unknown, chapters.ai_scanned_at,
import_sessions.ai_preview

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0009"
down_revision = "0008"  # G10 phải merge trước; xem header plan G9
branch_labels = None
depends_on = None

CATEGORIES = ("character", "location", "organization", "term", "rank", "realm", "item", "abbreviation")
SUGGESTION_STATUSES = ("pending", "accepted", "rejected")
HANVIET_SOURCES = ("ai", "confirmed", "learned", "manual")


def _ts(name: str, *, nullable: bool = False, default: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=sa.func.now() if default else None)


def upgrade() -> None:
    op.add_column("chapters", sa.Column("ai_scanned_at", sa.DateTime(timezone=True)))
    op.add_column("import_sessions",
                  sa.Column("ai_preview", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.create_table(
        "glossary_suggestions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", pg.UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("src_zh", sa.Text, nullable=False),
        sa.Column("dst_vi", sa.Text, nullable=False),
        sa.Column("category", pg.ENUM(*CATEGORIES, name="glossary_category", create_type=False), nullable=False),
        sa.Column("name_lang", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("context", sa.Text),
        sa.Column("confidence", sa.Integer, nullable=False, server_default="0"),
        sa.Column("occurrence_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("provider", sa.Text, nullable=False, server_default="deepseek"),
        sa.Column("model", sa.Text),
        sa.Column("status", sa.Enum(*SUGGESTION_STATUSES, name="suggestion_status"), nullable=False,
                  server_default="pending"),
        _ts("created_at"),
        _ts("updated_at"),
        _ts("decided_at", nullable=True, default=False),
        sa.UniqueConstraint("book_id", "src_zh", name="uq_glossary_suggestions_book_src"),
    )
    op.create_index("ix_glossary_suggestions_book_status", "glossary_suggestions", ["book_id", "status"])
    op.create_table(
        "hanviet_readings",
        sa.Column("char", sa.Text, primary_key=True),
        sa.Column("reading", sa.Text, primary_key=True),
        sa.Column("source", sa.Enum(*HANVIET_SOURCES, name="hanviet_source"), nullable=False),
        sa.Column("confidence", sa.Integer, nullable=False),
        _ts("created_at"),
    )
    op.create_table("hanviet_unknown", sa.Column("char", sa.Text, primary_key=True), _ts("created_at"))


def downgrade() -> None:
    op.drop_table("hanviet_unknown")
    op.drop_table("hanviet_readings")
    op.drop_table("glossary_suggestions")
    op.execute("DROP TYPE IF EXISTS hanviet_source")
    op.execute("DROP TYPE IF EXISTS suggestion_status")
    op.drop_column("import_sessions", "ai_preview")
    op.drop_column("chapters", "ai_scanned_at")
