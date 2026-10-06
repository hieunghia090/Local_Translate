"""chapter_revisions: snapshot, changed_idx, note, updated_at

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chapter_revisions", sa.Column("snapshot", pg.JSONB))
    op.add_column(
        "chapter_revisions",
        sa.Column("changed_idx", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("chapter_revisions", sa.Column("note", sa.Text))
    op.add_column(
        "chapter_revisions",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    for column in ("updated_at", "note", "changed_idx", "snapshot"):
        op.drop_column("chapter_revisions", column)
