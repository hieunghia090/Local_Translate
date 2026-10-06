"""honorific: chapters.register_*, segments.dst_model_raw / honorific_edits

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

ROUTES = ("ancient", "modern", "mixed", "unknown")


def upgrade() -> None:
    route = pg.ENUM(*ROUTES, name="register_route")
    route.create(op.get_bind(), checkfirst=True)
    col = pg.ENUM(*ROUTES, name="register_route", create_type=False)
    op.add_column("chapters", sa.Column("register_route", col))
    op.add_column("chapters", sa.Column("register_score", sa.Float))
    op.add_column("chapters", sa.Column("register_override", col))
    op.add_column("segments", sa.Column("dst_model_raw", sa.Text))
    op.add_column("segments", sa.Column("honorific_edits", pg.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("segments", "honorific_edits")
    op.drop_column("segments", "dst_model_raw")
    for c in ("register_override", "register_score", "register_route"):
        op.drop_column("chapters", c)
    op.execute("DROP TYPE IF EXISTS register_route")
