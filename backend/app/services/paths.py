from pathlib import Path

from app.config import get_settings


def books_root() -> Path:
    return get_settings().data_path / "books"


def chapter_source_path(slug: str, source_file: str) -> Path:
    return books_root() / slug / "source" / source_file
