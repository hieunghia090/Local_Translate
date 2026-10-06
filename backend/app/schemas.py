from pydantic import BaseModel, ConfigDict, Field


class ChapterEdit(BaseModel):
    key: str
    selected: bool | None = None
    title_vi: str | None = Field(default=None, max_length=500)


class ImportPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    split_rule: str | None = None
    split_regex: str | None = None
    encoding: str | None = None
    chapters: list[ChapterEdit] = Field(default_factory=list)

import uuid
from typing import Literal

from pydantic import field_validator, model_validator

from app.core.glossary import nfc

Genre = Literal["xianxia", "urban", "modern_war", "xuanhuan", "romance", "other"]


class GlossaryInit(BaseModel):
    source: Literal["none", "file", "copy"] = "none"
    file_id: str | None = None
    copy_from_book_id: uuid.UUID | None = None


_AiCategory = Literal["character", "location", "organization", "term", "rank", "realm", "item", "abbreviation"]


class PreviewEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dst_vi: str | None = Field(default=None, max_length=200)
    category: _AiCategory | None = None

    @field_validator("dst_vi")
    @classmethod
    def _dst(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Cần nhập đích Việt")
        return _no_ctrl(nfc(v.strip()), "Đích Việt") if v else v


class AiExtractInit(BaseModel):
    """Spec 02 mục 7, khối ai_extract của POST /books. Mã loại theo bảng loại của spec 04."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    provider: Literal["deepseek"] = "deepseek"
    model: Literal["deepseek-v4-pro", "deepseek-flash"] = "deepseek-v4-pro"
    chapters: int = Field(20, ge=5, le=100)
    categories: list[_AiCategory] = Field(
        default_factory=lambda: ["character", "organization", "realm", "location"])
    accepted_preview_ids: list[str] = Field(default_factory=list, max_length=2000)
    preview_edits: dict[str, PreviewEdit] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _categories_when_enabled(self):
        if self.enabled and not self.categories:
            raise ValueError("Chọn ít nhất một loại thuật ngữ")
        return self


class BookCreate(BaseModel):
    title_zh: str = Field(max_length=200)
    title_vi: str | None = Field(default=None, max_length=300)
    author: str | None = Field(default=None, max_length=200)
    genre: Genre = "other"
    note: str | None = None
    import_id: uuid.UUID | None = None
    run_config: dict = Field(default_factory=dict)
    foundation_prompt: str | None = None
    glossary: GlossaryInit = Field(default_factory=GlossaryInit)
    ai_extract: AiExtractInit = Field(default_factory=AiExtractInit)
    confirm_duplicate: bool = False

    @field_validator("title_zh", "title_vi", "author", "note", "foundation_prompt", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("title_vi", "author", "note", "foundation_prompt")
    @classmethod
    def _blank_to_none(cls, v):
        return v or None

    @field_validator("title_zh")
    @classmethod
    def _required(cls, v: str) -> str:
        if not v:
            raise ValueError("Cần nhập tên gốc của truyện")
        return v


class BookUpdate(BaseModel):
    """Chỉ các trường có gửi mới được đổi. Gửi chuỗi rỗng / null ở trường tuỳ chọn để xoá giá trị."""

    model_config = ConfigDict(extra="forbid")

    title_zh: str | None = Field(default=None, max_length=200)
    title_vi: str | None = Field(default=None, max_length=300)
    author: str | None = Field(default=None, max_length=200)
    genre: Genre | None = None
    note: str | None = None
    foundation_prompt: str | None = None
    run_config: dict | None = None

    @field_validator("title_zh", "title_vi", "author", "note", "foundation_prompt", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v

    @field_validator("title_zh")
    @classmethod
    def _title_not_blank(cls, v):
        if v is not None and not v:
            raise ValueError("Tên gốc không được để trống")
        return v


ChapterStatus = Literal["todo", "queued", "translating", "translated", "needs_review", "reviewed", "error"]


class BulkFilter(BaseModel):
    status: list[ChapterStatus] = Field(min_length=1)


class BulkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["translate", "retranslate", "mark_reviewed", "review"]
    chapter_ids: list[uuid.UUID] | None = None
    filter: BulkFilter | None = None
    keep_manual_edits: bool | None = None
    engine: Literal["ct2", "deepseek"] | None = None  # ghi đè engine cho lần dịch này (translate / retranslate)


class SegmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dst: str | None = Field(default=None, max_length=5000)
    revert: bool = False

    @model_validator(mode="after")
    def _one_action(self):
        if not self.revert and self.dst is None:
            raise ValueError("Cần gửi dst hoặc revert")
        return self


class TranslateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: bool = True
    keep_manual_edits: bool | None = None
    engine: Literal["ct2", "deepseek"] | None = None


class ChapterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_vi: str | None = Field(default=None, max_length=500)
    no: int | None = Field(default=None, ge=1, le=2_147_483_647)


class NewChapterJson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_zh: str | None = Field(default=None, max_length=200)
    content: str
    after_no: int | None = Field(default=None, ge=0, le=2_147_483_647)


import re as _re

_SRC_ZH = _re.compile(r"^[㐀-䶿一-鿿豈-﫿0-9·]+$")
GlossaryCategory = Literal["character", "location", "organization", "term", "rank", "realm", "item", "abbreviation"]
NameLang = Literal["zh", "ja", "foreign"]


def _clean_aliases(v):
    if v is None:
        return []
    if isinstance(v, str):
        v = v.split("|")
    elif not isinstance(v, (list, tuple)):
        raise ValueError("Alias phải là danh sách hoặc chuỗi ngăn cách bằng |")
    out: list[str] = []
    for a in v:
        a = nfc(str(a).strip())
        if a and a not in out:
            out.append(a)
    return out


_CTRL = _re.compile(r"[\t\r\n]")


def _no_ctrl(v: str | None, what: str) -> str | None:
    if v is not None and _CTRL.search(v):
        raise ValueError(f"{what} không được chứa tab hoặc xuống dòng")
    return v


def _check_aliases(v: list[str] | None) -> list[str] | None:
    for a in v or []:
        _no_ctrl(a, "Alias")
        if "|" in a:
            raise ValueError("Alias không được chứa dấu |")
    return v


class TermCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    src_zh: str = Field(max_length=100)
    dst_vi: str = Field(max_length=200)
    category: GlossaryCategory = "term"
    name_lang: NameLang | None = None
    notes: str | None = Field(default=None, max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    enabled: bool = True
    always_send: bool = False
    prompt_note: bool = False

    @field_validator("src_zh", "dst_vi", mode="before")
    @classmethod
    def _strip(cls, v):
        return nfc(v.strip()) if isinstance(v, str) else v

    @field_validator("src_zh")
    @classmethod
    def _src_han(cls, v: str) -> str:
        if not _SRC_ZH.match(v):
            raise ValueError("Nguồn Trung chỉ gồm chữ Hán, số và dấu ·")
        return v

    @field_validator("dst_vi")
    @classmethod
    def _dst_required(cls, v: str) -> str:
        if not v:
            raise ValueError("Cần nhập đích Việt")
        return _no_ctrl(v, "Đích Việt")

    @field_validator("notes")
    @classmethod
    def _notes(cls, v):
        return _no_ctrl(v, "Ghi chú")

    @field_validator("aliases", mode="before")
    @classmethod
    def _aliases(cls, v):
        return _clean_aliases(v)

    @field_validator("aliases")
    @classmethod
    def _aliases_ok(cls, v):
        return _check_aliases(v)


class TermUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dst_vi: str | None = Field(default=None, max_length=200)
    category: GlossaryCategory | None = None
    name_lang: NameLang | None = None
    notes: str | None = Field(default=None, max_length=500)
    aliases: list[str] | None = None
    enabled: bool | None = None
    always_send: bool | None = None
    prompt_note: bool | None = None

    @field_validator("dst_vi")
    @classmethod
    def _dst(cls, v):
        if v is not None and not v.strip():
            raise ValueError("Cần nhập đích Việt")
        return _no_ctrl(nfc(v.strip()) if v else v, "Đích Việt")

    @field_validator("notes")
    @classmethod
    def _notes(cls, v):
        return _no_ctrl(v, "Ghi chú")

    @field_validator("aliases", mode="before")
    @classmethod
    def _aliases(cls, v):
        return None if v is None else _clean_aliases(v)

    @field_validator("aliases")
    @classmethod
    def _aliases_ok(cls, v):
        return _check_aliases(v)


class CopyGlossary(BaseModel):
    from_book_id: uuid.UUID


class SnippetRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200)


Route = Literal["ancient", "modern", "mixed", "unknown"]


class ReapplyRequest(BaseModel):
    chapter_ids: list[uuid.UUID] | None = None


class RegisterRequest(BaseModel):
    route: Route | None


class HonorificConfig(BaseModel):
    kinship: bool = False
    pronoun: bool = False
    modern_stable: bool = False


class PreviewRequest(BaseModel):
    src: str = Field(max_length=5000)
    dst_raw: str = Field(max_length=5000)
    route: Route
    config: HonorificConfig


class FixIds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)


DeepSeekModelId = Literal["deepseek-v4-pro", "deepseek-flash"]
NoteType = Literal["correction", "context", "general"]


class FoundationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    foundation_prompt: str = Field(max_length=20000)

    @field_validator("foundation_prompt")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Prompt nền không được để trống")
        return v.strip()


class EstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["translate", "review"]
    chapter_ids: list[uuid.UUID] | None = None
    filter: BulkFilter | None = None
    model_id: DeepSeekModelId | None = None


def _note_text(v):
    if v is None:
        return v
    v = str(v).strip()
    if not v:
        raise ValueError("Ghi chú không được để trống")
    return v


class NoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: NoteType = "correction"
    content: str = Field(max_length=2000)

    @field_validator("content")
    @classmethod
    def _content(cls, v):
        return _note_text(v)


class NoteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: NoteType | None = None
    content: str | None = Field(default=None, max_length=2000)
    resolved: bool | None = None

    @field_validator("content")
    @classmethod
    def _content(cls, v):
        return _note_text(v)


class AiModelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=100)
    context_window: int | None = Field(default=None, ge=1024, le=10_000_000)
    max_output_tokens: int | None = Field(default=None, ge=256, le=1_000_000)
    price_in_per_mtok: float | None = Field(default=None, ge=0, le=1000)
    price_in_cached_per_mtok: float | None = Field(default=None, ge=0, le=1000)
    price_out_per_mtok: float | None = Field(default=None, ge=0, le=1000)
    enabled: bool | None = None


class ExportRequest(BaseModel):
    """Spec 03 mục 8: { scope, from_no?, to_no?, format, include_titles, keep_meta }."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["translated", "reviewed", "range"] = "translated"
    from_no: int | None = Field(default=None, ge=1, le=2_147_483_647)
    to_no: int | None = Field(default=None, ge=1, le=2_147_483_647)
    format: Literal["txt", "zip", "epub", "bilingual"] = "txt"
    include_titles: bool = True
    keep_meta: bool = False

    @model_validator(mode="after")
    def _range(self):
        if self.scope == "range":
            if self.from_no is None or self.to_no is None:
                raise ValueError("Cần nhập khoảng chương (từ # đến #)")
            if self.from_no > self.to_no:
                raise ValueError("Chương bắt đầu phải nhỏ hơn hoặc bằng chương kết thúc")
        return self


class HanvietUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readings: list[str] = Field(min_length=1, max_length=10)


EXTRACT_DEFAULT_CATEGORIES = ["character", "organization", "realm", "location"]


class ExtractScope(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mode: Literal["unscanned", "range", "first_n"] = "unscanned"
    from_: int | None = Field(default=None, alias="from", ge=1)
    to: int | None = Field(default=None, ge=1)
    n: int | None = Field(default=None, ge=1, le=100_000)

    @model_validator(mode="after")
    def _fields_for_mode(self):
        if self.mode == "range" and (self.from_ is None or self.to is None or self.from_ > self.to):
            raise ValueError("Khoảng chương cần from ≤ to")
        if self.mode == "first_n" and self.n is None:
            raise ValueError("Cần số chương n")
        return self


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["deepseek"] = "deepseek"
    model: DeepSeekModelId | None = None
    scope: ExtractScope = Field(default_factory=ExtractScope)
    categories: list[GlossaryCategory] = Field(default_factory=lambda: list(EXTRACT_DEFAULT_CATEGORIES), min_length=1)


class SuggestionAcceptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    dst_vi: str | None = Field(default=None, max_length=200)
    category: GlossaryCategory | None = None

    @field_validator("dst_vi")
    @classmethod
    def _dst(cls, v):
        if v is None:
            return v
        if not v.strip():
            raise ValueError("Cần nhập đích Việt")
        return _no_ctrl(nfc(v.strip()), "Đích Việt")


class SuggestionFilter(BaseModel):
    """Chọn các đề xuất pending theo bộ lọc phía server (không gửi danh sách id dài)."""

    model_config = ConfigDict(extra="forbid")

    min_confidence: int | None = Field(default=None, ge=0, le=100)
    max_confidence: int | None = Field(default=None, ge=0, le=100)
    exclude_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)


class SuggestionAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SuggestionAcceptItem] = Field(default_factory=list, max_length=2000)
    filter: SuggestionFilter | None = None

    @model_validator(mode="after")
    def _something(self):
        if not self.items and self.filter is None:
            raise ValueError("Cần gửi items hoặc filter")
        return self


class SuggestionReject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    filter: SuggestionFilter | None = None

    @model_validator(mode="after")
    def _something(self):
        if not self.ids and self.filter is None:
            raise ValueError("Cần gửi ids hoặc filter")
        return self


class GlossaryPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["deepseek"] = "deepseek"
    model: DeepSeekModelId = "deepseek-v4-pro"
    chapters: int = Field(20, ge=5, le=100)
    categories: list[GlossaryCategory] = Field(default_factory=lambda: list(EXTRACT_DEFAULT_CATEGORIES), min_length=1)
