"""books, chapters, segments, chapter_revisions, import_sessions

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Chép cố định ở đây, migration không import code app.
GENRES = ("xianxia", "urban", "modern_war", "xuanhuan", "romance", "other")
CHAPTER_STATUSES = ("todo", "queued", "translating", "translated", "needs_review", "reviewed", "error")
REVISION_KINDS = ("machine", "manual")
IMPORT_STATUSES = ("parsing", "ready", "failed")
ENUM_TYPES = ("import_status", "revision_kind", "chapter_status", "book_genre")

SEARCH_INDEXES = {
    "title_vi": "f_unaccent(lower(coalesce(title_vi, '')))",
    "title_zh": "title_zh",
    "author": "f_unaccent(lower(coalesce(author, '')))",
}


def _uuid_pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True)


def _ts(name: str, *, nullable: bool = False, default: bool = True) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable, server_default=sa.func.now() if default else None
    )


def _jsonb(name: str, default: str | None = None, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name, pg.JSONB, nullable=nullable, server_default=sa.text(f"'{default}'::jsonb") if default else None
    )


def upgrade() -> None:
    # unaccent() không IMMUTABLE nên không dùng trực tiếp trong index được.
    op.execute(
        "CREATE OR REPLACE FUNCTION f_unaccent(text) RETURNS text "
        "LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT "
        "AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$"
    )

    op.create_table(
        "books",
        _uuid_pk(),
        sa.Column("slug", sa.Text, nullable=False),
        sa.Column("title_zh", sa.Text, nullable=False),
        sa.Column("title_vi", sa.Text),
        sa.Column("author", sa.Text),
        sa.Column("genre", sa.Enum(*GENRES, name="book_genre"), nullable=False),
        sa.Column("note", sa.Text),
        sa.Column("cover_path", sa.Text),
        _jsonb("run_config"),
        sa.Column("foundation_prompt", sa.Text),
        _ts("created_at"),
        _ts("updated_at"),
        _ts("last_opened_at", nullable=True, default=False),
        sa.UniqueConstraint("slug", name="uq_books_slug"),
    )
    for name, expr in SEARCH_INDEXES.items():
        op.execute(f"CREATE INDEX ix_books_search_{name} ON books USING gin (({expr}) gin_trgm_ops)")

    op.create_table(
        "chapters",
        _uuid_pk(),
        sa.Column("book_id", pg.UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("no", sa.Integer, nullable=False),
        sa.Column("title_zh", sa.Text, nullable=False),
        sa.Column("title_vi", sa.Text),
        sa.Column(
            "status", sa.Enum(*CHAPTER_STATUSES, name="chapter_status"), nullable=False, server_default="todo"
        ),
        sa.Column("char_count", sa.Integer, nullable=False),
        sa.Column("source_hash", sa.Text, nullable=False),
        sa.Column("model_id", sa.Text),
        _jsonb("run_config_override", nullable=True),
        sa.Column("error", sa.Text),
        _ts("translated_at", nullable=True, default=False),
        _ts("reviewed_at", nullable=True, default=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("book_id", "no", name="uq_chapters_book_no", deferrable=True, initially="DEFERRED"),
    )
    op.create_index("ix_chapters_book_status", "chapters", ["book_id", "status"])

    op.create_table(
        "segments",
        sa.Column(
            "chapter_id", pg.UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("idx", sa.Integer, primary_key=True),
        sa.Column("src", sa.Text, nullable=False),
        sa.Column("is_meta", sa.Boolean, nullable=False),
        sa.Column("dst", sa.Text),
        sa.Column("dst_machine", sa.Text),
        sa.Column("edited", sa.Boolean, nullable=False, server_default=sa.false()),
        _jsonb("flags", "[]"),
    )

    op.create_table(
        "chapter_revisions",
        _uuid_pk(),
        sa.Column(
            "chapter_id", pg.UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.Enum(*REVISION_KINDS, name="revision_kind"), nullable=False),
        sa.Column("model_id", sa.Text),
        _jsonb("run_config", nullable=True),
        sa.Column("segments_changed", sa.Integer, nullable=False, server_default="0"),
        _ts("created_at"),
    )
    op.create_index("ix_chapter_revisions_chapter_id", "chapter_revisions", ["chapter_id"])

    op.create_table(
        "import_sessions",
        _uuid_pk(),
        sa.Column("status", sa.Enum(*IMPORT_STATUSES, name="import_status"), nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("split_rule", sa.Text, nullable=False),
        sa.Column("split_regex", sa.Text),
        sa.Column("encoding", sa.Text, nullable=False),
        sa.Column("source_name", sa.Text),
        _jsonb("files"),
        _jsonb("chapters", "[]"),
        _jsonb("file_errors", "[]"),
        _jsonb("edits", "{}"),
        sa.Column("suggested_title_zh", sa.Text),
        sa.Column("total_chars", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("analysis_token", pg.UUID(as_uuid=True)),
        _ts("created_at"),
        _ts("updated_at"),
        _ts("expires_at", default=False),
    )
    op.create_index("ix_import_sessions_expires_at", "import_sessions", ["expires_at"])


def downgrade() -> None:
    for table in ("import_sessions", "chapter_revisions", "segments", "chapters", "books"):
        op.drop_table(table)
    for enum in ENUM_TYPES:
        op.execute(f"DROP TYPE IF EXISTS {enum}")
    op.execute("DROP FUNCTION IF EXISTS f_unaccent(text)")
