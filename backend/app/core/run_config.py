from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ANCIENT = {"kinship": True, "pronoun": True, "modern_stable": False}
_MODERN = {"kinship": False, "pronoun": False, "modern_stable": True}
_OFF = {"kinship": False, "pronoun": False, "modern_stable": False}
GENRE_HONORIFIC: dict[str, dict[str, bool]] = {
    "xianxia": _ANCIENT,
    "xuanhuan": _ANCIENT,
    "urban": _MODERN,
    "modern_war": _MODERN,
    "romance": _MODERN,
    "other": _OFF,
}

DeepSeekModel = Literal["deepseek-v4-pro", "deepseek-flash"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Batch(_Strict):
    auto: bool = True
    size: int = Field(32, ge=1, le=64)  # đo trên M5: 32 nhanh ~1.7x so với 8


class Honorific(_Strict):
    kinship: bool = False
    pronoun: bool = False
    modern_stable: bool = False


class DeepSeekOptions(_Strict):
    model_id: DeepSeekModel = "deepseek-v4-pro"  # model dùng khi chọn DeepSeek; đồng bộ với RunConfig.model_id
    temperature: float = Field(0.3, ge=0, le=2)
    glossary_max_terms: int = Field(120, ge=1, le=1000)  # L4
    thinking: bool = False  # chỉ để khớp spec: request luôn tắt thinking (BR-8.6)
    chain_context: bool = True
    auto_extract_glossary: bool = True  # BR-8.17, làm ở G9
    concurrency: int = Field(3, ge=1, le=8)  # BR-8.15


class ReviewOptions(_Strict):
    auto_after_ct2: bool = False
    model_id: DeepSeekModel = "deepseek-flash"
    auto_apply: Literal["none", "high_confidence"] = "none"


class RunConfig(_Strict):
    engine: Literal["ct2", "deepseek"] = "ct2"
    model_id: Literal["HachimiMT-60", "deepseek-v4-pro", "deepseek-flash"] = "HachimiMT-60"
    beam: int = Field(2, ge=1, le=4)
    batch: Batch = Batch()
    chunk_mode: Literal["sentence", "paragraph"] = "paragraph"
    han_normalize: Literal["auto", "t2s", "none"] = "auto"
    honorific: Honorific = Honorific()
    deepseek: DeepSeekOptions = DeepSeekOptions()
    review: ReviewOptions = ReviewOptions()

    @model_validator(mode="after")
    def _engine_matches_model(self) -> "RunConfig":
        if (self.engine == "ct2") != (self.model_id == "HachimiMT-60"):
            raise ValueError("engine và model_id không khớp")
        if self.engine == "deepseek":
            self.deepseek.model_id = self.model_id
        return self


def default_run_config(genre: str) -> dict:
    honorific = GENRE_HONORIFIC.get(genre, _OFF)
    return RunConfig(honorific=Honorific(**honorific)).model_dump()


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def build_run_config(genre: str, override: dict | None) -> dict:
    """Mặc định theo thể loại, đè bằng phần người dùng gửi. Sai thì ném pydantic.ValidationError."""
    return RunConfig.model_validate(deep_merge(default_run_config(genre), override or {})).model_dump()


def deepseek_model(cfg: dict) -> str:
    """Model DeepSeek dùng cho cấu hình này: model_id khi engine = deepseek, không thì deepseek.model_id."""
    if cfg.get("engine") == "deepseek" and str(cfg.get("model_id", "")).startswith("deepseek"):
        return cfg["model_id"]
    return (cfg.get("deepseek") or {}).get("model_id") or "deepseek-v4-pro"


def with_engine(cfg: dict, engine: str) -> dict:
    """Bản sao cấu hình chạy bằng engine khác (ghi đè cho một lần dịch, không sửa cấu hình truyện)."""
    if engine == "ct2":
        return {**cfg, "engine": "ct2", "model_id": "HachimiMT-60"}
    return {**cfg, "engine": "deepseek", "model_id": deepseek_model({**cfg, "engine": "ct2"})}
