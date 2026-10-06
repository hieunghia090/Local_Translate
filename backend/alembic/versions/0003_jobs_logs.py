"""jobs, job_segments, worker_state, partitioned log_entries, chapters.source_file

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

JOB_KINDS = ("translate", "retranslate", "ai_extract", "honorific_reapply", "review")
JOB_ENGINES = ("ct2", "deepseek")
JOB_STATUSES = ("queued", "running", "paused", "done", "failed", "cancelled")
LOG_LEVELS = ("info", "warn", "error")
LOG_SOURCES = ("translate", "glossary", "review", "system")

ENSURE_PARTITIONS = """
CREATE OR REPLACE FUNCTION ensure_log_partitions(months_ahead int DEFAULT 2) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  first_month date := date_trunc('month', now())::date;
  start_d date;
  end_d date;
BEGIN
  FOR i IN 0..months_ahead LOOP
    start_d := (first_month + make_interval(months => i))::date;
    end_d := (first_month + make_interval(months => i + 1))::date;
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS %I PARTITION OF log_entries FOR VALUES FROM (%L) TO (%L)',
      'log_entries_' || to_char(start_d, 'YYYY_MM'), start_d, end_d
    );
  END LOOP;
END $$
"""


def _ts(name: str, *, nullable: bool = False, default: bool = True) -> sa.Column:
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable, server_default=sa.func.now() if default else None
    )


def _jsonb(name: str, default: str | None = None, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name, pg.JSONB, nullable=nullable, server_default=sa.text(f"'{default}'::jsonb") if default else None
    )


def upgrade() -> None:
    op.add_column("chapters", sa.Column("source_file", sa.Text))
    op.execute("UPDATE chapters SET source_file = lpad(no::text, 4, '0') || '.txt'")

    op.create_table(
        "jobs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("book_id", pg.UUID(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_id", pg.UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE")),
        sa.Column("kind", sa.Enum(*JOB_KINDS, name="job_kind"), nullable=False),
        sa.Column("engine", sa.Enum(*JOB_ENGINES, name="job_engine"), nullable=False),
        sa.Column("status", sa.Enum(*JOB_STATUSES, name="job_status"), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer, nullable=False, server_default="0"),
        sa.Column("position", sa.BigInteger, nullable=False),
        _jsonb("run_config"),
        _jsonb("options", "{}"),
        sa.Column("prev_chapter_status", sa.Text),
        sa.Column("source_hash", sa.Text),
        sa.Column("error", sa.Text),
        sa.Column("tokens_in", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        _ts("created_at"),
        _ts("updated_at"),
        _ts("started_at", nullable=True, default=False),
        _ts("finished_at", nullable=True, default=False),
    )
    op.create_index("ix_jobs_engine_status_position", "jobs", ["engine", "status", "position"])
    op.create_index("ix_jobs_book_id", "jobs", ["book_id"])
    op.create_index("ix_jobs_chapter_id", "jobs", ["chapter_id"])

    op.create_table(
        "job_segments",
        sa.Column("job_id", pg.UUID(as_uuid=True), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("idx", sa.Integer, primary_key=True),
        sa.Column("dst", sa.Text, nullable=False),
        _jsonb("flags", "[]"),
    )

    op.create_table(
        "worker_state",
        sa.Column("engine", pg.ENUM(*JOB_ENGINES, name="job_engine", create_type=False), primary_key=True),
        sa.Column("paused", sa.Boolean, nullable=False, server_default=sa.false()),
        _ts("updated_at"),
    )
    op.execute("INSERT INTO worker_state (engine) VALUES ('ct2'), ('deepseek')")

    op.create_table(
        "log_entries",
        sa.Column("id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("book_id", pg.UUID(as_uuid=True)),
        sa.Column("chapter_id", pg.UUID(as_uuid=True)),
        sa.Column("chapter_no", sa.Integer),
        sa.Column("job_id", pg.UUID(as_uuid=True)),
        sa.Column("level", sa.Enum(*LOG_LEVELS, name="log_level"), nullable=False),
        sa.Column("source", sa.Enum(*LOG_SOURCES, name="log_source"), nullable=False),
        sa.Column("provider", sa.Text),
        sa.Column("model", sa.Text),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("tokens_in", sa.Integer),
        sa.Column("tokens_out", sa.Integer),
        sa.Column("tokens_in_cached", sa.Integer),
        sa.Column("tokens_in_est", sa.Integer),
        sa.Column("tokens_out_est", sa.Integer),
        sa.Column("cost_usd", sa.Numeric(12, 6)),
        sa.Column("latency_ms", sa.Integer),
        _jsonb("params", "{}"),
        _jsonb("detail", "{}"),
        sa.PrimaryKeyConstraint("id", "ts"),
        postgresql_partition_by="RANGE (ts)",
    )
    op.execute("CREATE INDEX ix_log_entries_book_ts ON log_entries (book_id, ts DESC)")
    op.execute("CREATE INDEX ix_log_entries_book_level_ts ON log_entries (book_id, level, ts DESC)")
    op.execute("CREATE INDEX ix_log_entries_message_trgm ON log_entries USING gin (message gin_trgm_ops)")
    op.execute(ENSURE_PARTITIONS)
    op.execute("SELECT ensure_log_partitions(2)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS log_entries CASCADE")
    op.execute("DROP FUNCTION IF EXISTS ensure_log_partitions(int)")
    for table in ("worker_state", "job_segments", "jobs"):
        op.drop_table(table)
    for enum in ("log_source", "log_level", "job_status", "job_engine", "job_kind"):
        op.execute(f"DROP TYPE IF EXISTS {enum}")
    op.drop_column("chapters", "source_file")
