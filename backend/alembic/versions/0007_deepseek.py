"""deepseek: review_fixes, chapter_notes, ai_models, worker_state.paused_reason, chapters.title_vi_edited

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

FIX_STATUSES = ("pending", "applied", "rejected")
NOTE_TYPES = ("correction", "context", "general")
# Chép cố định (migration không import app): giá MẪU, USD / 1 triệu token.
MODELS = [
    {"id": "deepseek-v4-pro", "provider": "deepseek", "label": "DeepSeek V4 Pro", "context_window": 131072,
     "max_output_tokens": 8192, "price_in_per_mtok": 0.27, "price_in_cached_per_mtok": 0.07, "price_out_per_mtok": 1.10},
    {"id": "deepseek-flash", "provider": "deepseek", "label": "DeepSeek Flash", "context_window": 131072,
     "max_output_tokens": 8192, "price_in_per_mtok": 0.07, "price_in_cached_per_mtok": 0.02, "price_out_per_mtok": 0.28},
]


def _ts(name: str, *, nullable: bool = False, default: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable, server_default=sa.func.now() if default else None)


def upgrade() -> None:
    op.add_column("worker_state", sa.Column("paused_reason", sa.Text))
    op.add_column("chapters", sa.Column("title_vi_edited", sa.Boolean, nullable=False, server_default=sa.false()))

    op.create_table(
        "review_fixes",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("chapter_id", pg.UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("segment_idx", sa.Integer, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("before", sa.Text, nullable=False),
        sa.Column("after", sa.Text, nullable=False),
        sa.Column("reason", sa.Text),
        sa.Column("confidence", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.Enum(*FIX_STATUSES, name="review_fix_status"), nullable=False, server_default="pending"),
        sa.Column("model_id", sa.Text),
        _ts("created_at"),
        _ts("decided_at", nullable=True, default=False),
    )
    op.create_index("ix_review_fixes_chapter_status", "review_fixes", ["chapter_id", "status"])

    op.create_table(
        "chapter_notes",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("chapter_id", pg.UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.Enum(*NOTE_TYPES, name="note_type"), nullable=False, server_default="correction"),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default=sa.false()),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_chapter_notes_chapter_id", "chapter_notes", ["chapter_id"])

    models = op.create_table(
        "ai_models",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("context_window", sa.Integer, nullable=False),
        sa.Column("max_output_tokens", sa.Integer, nullable=False),
        sa.Column("price_in_per_mtok", sa.Numeric(10, 4), nullable=False),
        sa.Column("price_in_cached_per_mtok", sa.Numeric(10, 4), nullable=False),
        sa.Column("price_out_per_mtok", sa.Numeric(10, 4), nullable=False),
        sa.Column("prices_are_samples", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        _ts("updated_at"),
    )
    op.bulk_insert(models, MODELS)


def downgrade() -> None:
    op.drop_table("ai_models")
    op.drop_table("chapter_notes")
    op.drop_table("review_fixes")
    op.execute("DROP TYPE IF EXISTS note_type")
    op.execute("DROP TYPE IF EXISTS review_fix_status")
    op.drop_column("chapters", "title_vi_edited")
    op.drop_column("worker_state", "paused_reason")
