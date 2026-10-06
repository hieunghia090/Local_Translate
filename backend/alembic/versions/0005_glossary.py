"""glossary_terms, segments.glossary_hits, job_segments.glossary_hits

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

CATEGORIES = ("character", "location", "organization", "term", "rank", "realm", "item", "abbreviation")
ORIGINS = ("manual", "import", "ai", "copied")
EMPTY = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "glossary_terms",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", pg.UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("src_zh", sa.Text, nullable=False),
        sa.Column("dst_vi", sa.Text, nullable=False),
        sa.Column("category", sa.Enum(*CATEGORIES, name="glossary_category"), nullable=False),
        sa.Column("name_lang", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("aliases", pg.JSONB, nullable=False, server_default=EMPTY),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("predictable", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("always_send", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("prompt_note", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("miss_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("occurrence_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("origin", sa.Enum(*ORIGINS, name="glossary_origin"), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("book_id", "src_zh", name="uq_glossary_book_src"),
    )
    op.create_index("ix_glossary_terms_book_id", "glossary_terms", ["book_id"])
    op.add_column("segments", sa.Column("glossary_hits", pg.JSONB, nullable=False, server_default=EMPTY))
    op.add_column("job_segments", sa.Column("glossary_hits", pg.JSONB, nullable=False, server_default=EMPTY))


def downgrade() -> None:
    op.drop_column("job_segments", "glossary_hits")
    op.drop_column("segments", "glossary_hits")
    op.drop_table("glossary_terms")
    op.execute("DROP TYPE IF EXISTS glossary_origin")
    op.execute("DROP TYPE IF EXISTS glossary_category")
