from typing import get_args

import pytest
from pydantic import ValidationError

from app.core.run_config import GENRE_HONORIFIC
from app.models import GENRES
from app.schemas import BookCreate, Genre


def test_genre_lists_agree():
    assert set(get_args(Genre)) == set(GENRES) == set(GENRE_HONORIFIC)


@pytest.mark.parametrize("title", ["", "   "])
def test_title_zh_required(title):
    # AC-2.7 (phần API)
    with pytest.raises(ValidationError):
        BookCreate(title_zh=title)


def test_optional_text_fields_blank_to_none():
    b = BookCreate(title_zh=" 大宋有种 ", title_vi="  ", author="")
    assert (b.title_zh, b.title_vi, b.author) == ("大宋有种", None, None)


@pytest.mark.parametrize("field,value", [
    ("dst_vi", "A\tB"), ("dst_vi", "A\nB"), ("notes", "a\rb"), ("aliases", ["x\ty"]), ("aliases", ["a|b"]),
])
def test_term_rejects_control_chars_and_pipe(field, value):
    from app.schemas import TermCreate, TermUpdate

    with pytest.raises(ValidationError) as e:
        TermCreate(src_zh="赵", **{"dst_vi": "A", field: value} if field != "dst_vi" else {"dst_vi": value})
    assert "không được chứa" in str(e.value)
    with pytest.raises(ValidationError):
        TermUpdate(**{field: value})
