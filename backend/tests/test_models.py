import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Base, Book, Chapter, ChapterRevision, Export, Segment
from helpers import seed_book

pytestmark = pytest.mark.db


import re

_MIGRATION_ONLY_INDEXES = {"ix_log_entries_book_ts", "ix_log_entries_book_level_ts", "ix_log_entries_message_trgm"}
_PARTITION = re.compile(r"^log_entries_\d{4}_\d{2}$")


def _skip_search_indexes(obj, name, type_, reflected, compare_to):
    # Index biểu thức và partition log chỉ khai báo trong migration.
    if type_ == "table" and name and _PARTITION.match(name):
        return False
    if type_ == "index" and name and (name.startswith(("ix_books_search_", "ix_chapters_search_")) or name in _MIGRATION_ONLY_INDEXES):
        return False
    return True


async def test_models_match_migrations(migrated_db):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    def diff(conn):
        ctx = MigrationContext.configure(conn, opts={"include_object": _skip_search_indexes})
        return compare_metadata(ctx, Base.metadata)

    async with db.get_engine().connect() as conn:
        assert await conn.run_sync(diff) == []


async def test_search_indexes_and_unaccent_function(migrated_db):
    async with db.get_engine().connect() as conn:
        names = set((await conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'books'"))).scalars())
        folded = await conn.scalar(text("SELECT f_unaccent(lower('Đại Tống'))"))
    assert {"ix_books_search_title_vi", "ix_books_search_title_zh", "ix_books_search_author"} <= names
    assert folded == "dai tong"


async def test_book_ids_are_uuid7(clean_db):
    book_id = await seed_book()
    assert book_id.version == 7


async def test_chapter_no_unique_per_book(clean_db):
    book_id = await seed_book(statuses=["todo"])
    async with db.get_sessionmaker()() as s:
        s.add(Chapter(book_id=book_id, no=1, title_zh="重复", char_count=1, source_hash="y"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_deleting_book_cascades(clean_db):
    book_id = await seed_book(statuses=["todo"])
    async with db.get_sessionmaker()() as s:
        chapter = (await s.scalars(select(Chapter).where(Chapter.book_id == book_id))).one()
        s.add(Segment(chapter_id=chapter.id, idx=0, src="第1章", is_meta=False))
        s.add(ChapterRevision(chapter_id=chapter.id, kind="machine", segments_changed=1))
        await s.commit()
        await s.delete(await s.get(Book, book_id))
        await s.commit()
        for model in (Chapter, Segment, ChapterRevision):
            assert await s.scalar(select(func.count()).select_from(model)) == 0


async def test_chapter_defaults(clean_db):
    book_id = await seed_book(statuses=["todo"])
    async with db.get_sessionmaker()() as s:
        ch = (await s.scalars(select(Chapter).where(Chapter.book_id == book_id))).one()
    assert ch.status == "todo" and ch.model_id is None and ch.created_at.tzinfo is not None


def test_migration_downgrade_then_upgrade(migrated_db):
    from alembic import command

    from conftest import alembic_config

    command.downgrade(alembic_config(), "0001")
    command.upgrade(alembic_config(), "head")


async def test_log_partitions_exist_for_this_and_next_months(migrated_db):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    expected = f"log_entries_{now:%Y_%m}"
    async with db.get_engine().begin() as conn:
        await conn.execute(text("SELECT ensure_log_partitions(2)"))
        names = set(
            (await conn.execute(text(
                "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
                "WHERE i.inhparent = 'log_entries'::regclass"
            ))).scalars()
        )
    assert expected in names
    assert len(names) >= 3


async def test_worker_state_rows_seeded(migrated_db):
    async with db.get_engine().connect() as conn:
        rows = dict((await conn.execute(text("SELECT engine::text, paused FROM worker_state"))).all())
    assert rows == {"ct2": False, "deepseek": False}


async def test_job_defaults(clean_db):
    from app.models import Job

    book_id = await seed_book(statuses=["todo"])
    async with db.get_sessionmaker()() as s:
        ch = (await s.scalars(select(Chapter).where(Chapter.book_id == book_id))).one()
        s.add(Job(book_id=book_id, chapter_id=ch.id, kind="translate", engine="ct2", position=1, run_config={}))
        await s.commit()
        job = (await s.scalars(select(Job))).one()
    assert (job.status, job.progress, job.tokens_in, job.options, job.attempts) == ("queued", 0, 0, {}, 0)


async def test_glossary_term_unique_per_book(clean_db):
    from app.models import GlossaryTerm

    book_id = await seed_book()
    async with db.get_sessionmaker()() as s:
        s.add(GlossaryTerm(book_id=book_id, src_zh="赵楷", dst_vi="Triệu Khải", category="character"))
        await s.commit()
        term = (await s.scalars(select(GlossaryTerm))).one()
        assert (term.enabled, term.aliases, term.origin, term.occurrence_count, term.predictable) == (True, [], "manual", 0, False)
        s.add(GlossaryTerm(book_id=book_id, src_zh="赵楷", dst_vi="Khác", category="character"))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_segment_glossary_hits_default(clean_db):
    book_id = await seed_book(statuses=["todo"])
    async with db.get_sessionmaker()() as s:
        ch = (await s.scalars(select(Chapter).where(Chapter.book_id == book_id))).one()
        s.add(Segment(chapter_id=ch.id, idx=0, src="赵楷", is_meta=False))
        await s.commit()
        seg = (await s.scalars(select(Segment))).one()
    assert seg.glossary_hits == []


async def test_honorific_columns(clean_db):
    book_id = await seed_book(statuses=["todo"])
    async with db.get_sessionmaker()() as s:
        ch = (await s.scalars(select(Chapter).where(Chapter.book_id == book_id))).one()
        assert (ch.register_route, ch.register_score, ch.register_override) == (None, None, None)
        ch.register_override = "modern"
        s.add(Segment(chapter_id=ch.id, idx=0, src="他", is_meta=False))
        await s.commit()
        seg = (await s.scalars(select(Segment))).one()
    assert seg.dst_model_raw is None and seg.honorific_edits == []


async def test_export_row_defaults_and_cascade(clean_db):
    book_id = await seed_book()
    async with db.get_sessionmaker()() as s:
        s.add(Export(book_id=book_id, scope="reviewed", format="txt", include_titles=True, keep_meta=False))
        await s.commit()
        exp = (await s.scalars(select(Export))).one()
        assert (exp.status, exp.chapters, exp.skipped, exp.file_name, exp.finished_at) == ("running", 0, 0, None, None)
        await s.delete(await s.get(Book, book_id))
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(Export)) == 0


async def test_export_rejects_unknown_scope(clean_db):
    book_id = await seed_book()
    async with db.get_sessionmaker()() as s:
        s.add(Export(book_id=book_id, scope="all", format="txt", include_titles=True, keep_meta=False))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_chapter_search_indexes(migrated_db):
    async with db.get_engine().connect() as conn:
        names = set((await conn.execute(text("SELECT indexname FROM pg_indexes WHERE tablename = 'chapters'"))).scalars())
    assert {"ix_chapters_search_title_vi", "ix_chapters_search_title_zh"} <= names
