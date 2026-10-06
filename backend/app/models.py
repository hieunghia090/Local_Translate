import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, Enum, Float, ForeignKey, Index, Integer, Numeric, Text, UniqueConstraint, false, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.ids import uuid7

GENRES = ("xianxia", "urban", "modern_war", "xuanhuan", "romance", "other")
CHAPTER_STATUSES = ("todo", "queued", "translating", "translated", "needs_review", "reviewed", "error")
DONE_STATUSES = ("translated", "needs_review", "reviewed")
REVISION_KINDS = ("machine", "manual")
IMPORT_STATUSES = ("parsing", "ready", "failed")
JOB_KINDS = ("translate", "retranslate", "ai_extract", "honorific_reapply", "review")
JOB_ENGINES = ("ct2", "deepseek")
JOB_STATUSES = ("queued", "running", "paused", "done", "failed", "cancelled")
ACTIVE_JOB_STATUSES = ("queued", "running", "paused")
FINISHED_JOB_STATUSES = ("done", "failed", "cancelled")
LOG_LEVELS = ("info", "warn", "error")
LOG_SOURCES = ("translate", "glossary", "review", "system")
GLOSSARY_CATEGORIES = ("character", "location", "organization", "term", "rank", "realm", "item", "abbreviation")
GLOSSARY_ORIGINS = ("manual", "import", "ai", "copied")
NAME_LANGS = ("zh", "ja", "foreign")
REVIEW_FIX_TYPES = ("name_mismatch", "missing_content", "mistranslation", "honorific", "grammar")
REVIEW_FIX_STATUSES = ("pending", "applied", "rejected")
NOTE_TYPES = ("correction", "context", "general")
REGISTER_ROUTES = ("ancient", "modern", "mixed", "unknown")
SUGGESTION_STATUSES = ("pending", "accepted", "rejected")
HANVIET_SOURCES = ("ai", "confirmed", "learned", "manual")
EXPORT_SCOPES = ("translated", "reviewed", "range")
EXPORT_FORMATS = ("txt", "zip", "epub", "bilingual")
EXPORT_STATUSES = ("running", "done", "failed")

_EMPTY_LIST = text("'[]'::jsonb")
_EMPTY_OBJECT = text("'{}'::jsonb")


def utcnow() -> datetime:
    """Thời điểm hiện tại phía Python. Dùng thay func.now() khi còn đọc lại thuộc tính sau commit."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _updated() -> Mapped[datetime]:
    # Lưu ý: sau commit, đừng đọc updated_at mà không refresh (giá trị do server sinh, bị expire).
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Book(Base):
    __tablename__ = "books"
    __table_args__ = (UniqueConstraint("slug", name="uq_books_slug"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(Text)
    title_zh: Mapped[str] = mapped_column(Text)
    title_vi: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(Text)
    genre: Mapped[str] = mapped_column(Enum(*GENRES, name="book_genre"))
    note: Mapped[str | None] = mapped_column(Text)
    cover_path: Mapped[str | None] = mapped_column(Text)
    run_config: Mapped[dict] = mapped_column(JSONB)
    foundation_prompt: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("book_id", "no", name="uq_chapters_book_no", deferrable=True, initially="DEFERRED"),
        Index("ix_chapters_book_status", "book_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    no: Mapped[int] = mapped_column(Integer)
    title_zh: Mapped[str] = mapped_column(Text)
    title_vi: Mapped[str | None] = mapped_column(Text)
    title_vi_edited: Mapped[bool] = mapped_column(Boolean, server_default=false())  # BR-8.8: người dùng đã sửa tiêu đề
    status: Mapped[str] = mapped_column(Enum(*CHAPTER_STATUSES, name="chapter_status"), server_default="todo")
    char_count: Mapped[int] = mapped_column(Integer)
    source_hash: Mapped[str] = mapped_column(Text)
    source_file: Mapped[str | None] = mapped_column(Text)  # tên file trong books/<slug>/source/
    model_id: Mapped[str | None] = mapped_column(Text)
    run_config_override: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    register_route: Mapped[str | None] = mapped_column(Enum(*REGISTER_ROUTES, name="register_route"))
    register_score: Mapped[float | None] = mapped_column(Float)
    register_override: Mapped[str | None] = mapped_column(Enum(*REGISTER_ROUTES, name="register_route"))
    ai_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # BR-4.11
    translated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Segment(Base):
    __tablename__ = "segments"

    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    src: Mapped[str] = mapped_column(Text)
    is_meta: Mapped[bool] = mapped_column(Boolean)
    dst: Mapped[str | None] = mapped_column(Text)
    dst_machine: Mapped[str | None] = mapped_column(Text)
    dst_mt: Mapped[str | None] = mapped_column(Text)  # bản HachimiMT mới nhất (spec 06 BR-6.10)
    dst_ai: Mapped[str | None] = mapped_column(Text)  # bản DeepSeek mới nhất
    edited: Mapped[bool] = mapped_column(Boolean, server_default=false())
    flags: Mapped[list] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    glossary_hits: Mapped[list] = mapped_column(JSONB, server_default=_EMPTY_LIST)  # id các term đã áp
    dst_model_raw: Mapped[str | None] = mapped_column(Text)  # output sau khôi phục placeholder, trước chuẩn hoá xưng hô
    honorific_edits: Mapped[list] = mapped_column(JSONB, server_default=_EMPTY_LIST)


class ChapterRevision(Base):
    __tablename__ = "chapter_revisions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(Enum(*REVISION_KINDS, name="revision_kind"))
    model_id: Mapped[str | None] = mapped_column(Text)
    run_config: Mapped[dict | None] = mapped_column(JSONB)
    segments_changed: Mapped[int] = mapped_column(Integer, server_default="0")
    snapshot: Mapped[list | None] = mapped_column(JSONB)  # [{idx, src, dst, dst_machine, edited}] các câu không phải meta
    changed_idx: Mapped[list] = mapped_column(JSONB, server_default=_EMPTY_LIST)  # câu đã đổi (revision manual)
    note: Mapped[str | None] = mapped_column(Text)  # vd. "Khôi phục bản 14:22 03/10"
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ImportSession(Base):
    """Phiên phân tích file trước khi tạo truyện. JSONB: gán giá trị mới thay vì sửa tại chỗ."""

    __tablename__ = "import_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    status: Mapped[str] = mapped_column(Enum(*IMPORT_STATUSES, name="import_status"))
    mode: Mapped[str] = mapped_column(Text)
    split_rule: Mapped[str] = mapped_column(Text)
    split_regex: Mapped[str | None] = mapped_column(Text)
    encoding: Mapped[str] = mapped_column(Text)
    source_name: Mapped[str | None] = mapped_column(Text)
    files: Mapped[list] = mapped_column(JSONB)  # [{name, path (tương đối) | null, size}]
    chapters: Mapped[list] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    file_errors: Mapped[list] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    edits: Mapped[dict] = mapped_column(JSONB, server_default=_EMPTY_OBJECT)  # {key: {selected?, title_vi?}}
    ai_preview: Mapped[list] = mapped_column(JSONB, server_default=_EMPTY_LIST)  # kết quả "Chạy thử" AI (spec 02 mục 7)
    suggested_title_zh: Mapped[str | None] = mapped_column(Text)
    total_chars: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    analysis_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # lần phân tích đang được phép ghi kết quả
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_engine_status_position", "engine", "status", "position"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(Enum(*JOB_KINDS, name="job_kind"))
    engine: Mapped[str] = mapped_column(Enum(*JOB_ENGINES, name="job_engine"))
    status: Mapped[str] = mapped_column(Enum(*JOB_STATUSES, name="job_status"), server_default="queued")
    progress: Mapped[int] = mapped_column(Integer, server_default="0")
    position: Mapped[int] = mapped_column(BigInteger)
    run_config: Mapped[dict] = mapped_column(JSONB)  # bản chụp lúc tạo job (AC-3.7)
    options: Mapped[dict] = mapped_column(JSONB, server_default=_EMPTY_OBJECT)  # vd. keep_manual_edits
    prev_chapter_status: Mapped[str | None] = mapped_column(Text)  # để huỷ thì trả chương về như cũ
    source_hash: Mapped[str | None] = mapped_column(Text)  # bản gốc lúc bắt đầu dịch
    error: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobSegment(Base):
    """Kết quả dịch tạm của từng câu. Chép sang `segments` khi job xong cả chương."""

    __tablename__ = "job_segments"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    dst: Mapped[str] = mapped_column(Text)
    flags: Mapped[list] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    glossary_hits: Mapped[list] = mapped_column(JSONB, server_default=_EMPTY_LIST)


class WorkerState(Base):
    __tablename__ = "worker_state"

    engine: Mapped[str] = mapped_column(Enum(*JOB_ENGINES, name="job_engine"), primary_key=True)
    paused: Mapped[bool] = mapped_column(Boolean, server_default=false())
    paused_reason: Mapped[str | None] = mapped_column(Text)  # auth | no_key | token_anomaly | failures | NULL (tạm dừng tay)
    updated_at: Mapped[datetime] = _updated()


class LogEntry(Base):
    __tablename__ = "log_entries"
    __table_args__ = {"postgresql_partition_by": "RANGE (ts)"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, default=utcnow)
    book_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    chapter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    chapter_no: Mapped[int | None] = mapped_column(Integer)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    level: Mapped[str] = mapped_column(Enum(*LOG_LEVELS, name="log_level"))
    source: Mapped[str] = mapped_column(Enum(*LOG_SOURCES, name="log_source"))
    provider: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    tokens_in_cached: Mapped[int | None] = mapped_column(Integer)
    tokens_in_est: Mapped[int | None] = mapped_column(Integer)
    tokens_out_est: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    params: Mapped[dict] = mapped_column(JSONB, server_default=_EMPTY_OBJECT)
    detail: Mapped[dict] = mapped_column(JSONB, server_default=_EMPTY_OBJECT)


class GlossaryTerm(Base):
    __tablename__ = "glossary_terms"
    __table_args__ = (UniqueConstraint("book_id", "src_zh", name="uq_glossary_book_src"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    src_zh: Mapped[str] = mapped_column(Text)
    dst_vi: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Enum(*GLOSSARY_CATEGORIES, name="glossary_category"))
    name_lang: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    predictable: Mapped[bool] = mapped_column(Boolean, server_default=false())
    always_send: Mapped[bool] = mapped_column(Boolean, server_default=false())
    prompt_note: Mapped[bool] = mapped_column(Boolean, server_default=false())
    miss_count: Mapped[int] = mapped_column(Integer, server_default="0")
    occurrence_count: Mapped[int] = mapped_column(Integer, server_default="0")
    origin: Mapped[str] = mapped_column(Enum(*GLOSSARY_ORIGINS, name="glossary_origin"), server_default="manual")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ReviewFix(Base):
    """Đề xuất sửa một câu của DeepSeek (spec 08 mục 5)."""

    __tablename__ = "review_fixes"
    __table_args__ = (Index("ix_review_fixes_chapter_status", "chapter_id", "status"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"))
    segment_idx: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(Text)
    before: Mapped[str] = mapped_column(Text)
    after: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[int] = mapped_column(Integer, server_default="0")
    status: Mapped[str] = mapped_column(Enum(*REVIEW_FIX_STATUSES, name="review_fix_status"), server_default="pending")
    model_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChapterNote(Base):
    """Ghi chú sửa lỗi cho lần dịch lại (US-8.7, AC-8.10)."""

    __tablename__ = "chapter_notes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(Enum(*NOTE_TYPES, name="note_type"), server_default="correction")
    content: Mapped[str] = mapped_column(Text)
    resolved: Mapped[bool] = mapped_column(Boolean, server_default=false())
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AiModel(Base):
    """Model AI và bảng giá (BR-8.25). Giá tính bằng USD cho 1 triệu token."""

    __tablename__ = "ai_models"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    context_window: Mapped[int] = mapped_column(Integer)
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    price_in_per_mtok: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    price_in_cached_per_mtok: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    price_out_per_mtok: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    prices_are_samples: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    updated_at: Mapped[datetime] = _updated()


class Export(Base):
    """Một lần xuất bản dịch (spec 03 mục 7). File nằm ở books/<slug>/exports/<file_name>."""

    __tablename__ = "exports"
    __table_args__ = (
        CheckConstraint("scope IN ('translated', 'reviewed', 'range')", name="ck_exports_scope"),
        CheckConstraint("format IN ('txt', 'zip', 'epub', 'bilingual')", name="ck_exports_format"),
        CheckConstraint("status IN ('running', 'done', 'failed')", name="ck_exports_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    scope: Mapped[str] = mapped_column(Text)
    format: Mapped[str] = mapped_column(Text)
    from_no: Mapped[int | None] = mapped_column(Integer)
    to_no: Mapped[int | None] = mapped_column(Integer)
    include_titles: Mapped[bool] = mapped_column(Boolean)
    keep_meta: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(Text, server_default="running")
    file_name: Mapped[str | None] = mapped_column(Text)
    chapters: Mapped[int] = mapped_column(Integer, server_default="0")
    skipped: Mapped[int] = mapped_column(Integer, server_default="0")
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GlossarySuggestion(Base):
    """Đề xuất thuật ngữ từ AI, chờ người dùng duyệt (spec 04 mục 6, BR-4.8)."""

    __tablename__ = "glossary_suggestions"
    __table_args__ = (
        UniqueConstraint("book_id", "src_zh", name="uq_glossary_suggestions_book_src"),
        Index("ix_glossary_suggestions_book_status", "book_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    src_zh: Mapped[str] = mapped_column(Text)
    dst_vi: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Enum(*GLOSSARY_CATEGORIES, name="glossary_category"))
    name_lang: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[int] = mapped_column(Integer, server_default="0")
    occurrence_count: Mapped[int] = mapped_column(Integer, server_default="0")
    provider: Mapped[str] = mapped_column(Text, server_default="deepseek")
    model: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Enum(*SUGGESTION_STATUSES, name="suggestion_status"), server_default="pending")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HanvietReading(Base):
    """Âm Hán Việt theo chữ (BR-4.17). Một chữ có thể có nhiều âm."""

    __tablename__ = "hanviet_readings"

    char: Mapped[str] = mapped_column(Text, primary_key=True)
    reading: Mapped[str] = mapped_column(Text, primary_key=True)  # viết thường, NFC
    source: Mapped[str] = mapped_column(Enum(*HANVIET_SOURCES, name="hanviet_source"))
    confidence: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class AppMeta(Base):
    """Cặp khoá-giá trị nhỏ của app, ví dụ sha256 file seed Hán Việt đã nạp."""

    __tablename__ = "app_meta"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = _updated()


class HanvietUnknown(Base):
    """Chữ DeepSeek trả rỗng khi hỏi âm: không hỏi lại (mục 6a, bước 3)."""

    __tablename__ = "hanviet_unknown"

    char: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = _created()
