from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import yaml

DATA = Path(__file__).parent / "data"


@dataclass(frozen=True)
class Rule:
    src: str
    target: str
    forms: tuple[str, ...]
    rule: str

    def with_target(self, target: str, *, drop_forms: tuple[str, ...] = ()) -> "Rule":
        return replace(self, target=target, forms=tuple(f for f in self.forms if f not in drop_forms and f != target))


@lru_cache
def kinship() -> tuple[tuple[Rule, ...], tuple[str, ...]]:
    data = yaml.safe_load((DATA / "kinship.yaml").read_text(encoding="utf-8"))
    rules = tuple(Rule(src, v["target"], tuple(v["forms"]), f"kinship.{src}") for src, v in data["terms"].items())
    return rules, tuple(data.get("blockers") or ())


@lru_cache
def markers() -> dict[str, tuple[str, ...]]:
    data = yaml.safe_load((DATA / "markers.yaml").read_text(encoding="utf-8"))
    return {k: tuple(v) for k, v in data.items()}


NARRATIVE = (
    Rule("他们", "bọn hắn", ("các anh ấy", "bọn họ", "họ"), "pronoun.他们"),
    Rule("他", "hắn", ("anh ta", "anh ấy", "cậu ta", "ông ta"), "pronoun.他"),
    Rule("她", "nàng", ("cô ta", "cô ấy", "chị ta", "chị ấy", "bà ta"), "pronoun.她"),
)
DIALOGUE = (
    Rule("你们", "các ngươi", ("các anh", "các cậu", "các bạn"), "pronoun.你们"),
    Rule("我们", "chúng ta", ("chúng tôi", "bọn tôi"), "pronoun.我们"),
    Rule("咱们", "chúng ta", ("chúng tôi", "bọn tôi"), "pronoun.咱们"),
    Rule("您", "ngài", ("anh", "ông", "bà"), "pronoun.您"),
    Rule("你", "ngươi", ("anh", "em", "cậu", "bạn", "mày", "cô"), "pronoun.你"),
    Rule("我", "ta", ("tôi", "tớ", "tao"), "pronoun.我"),
)
PRONOUN_BLOCKERS = ("其他", "她们")  # chứa 他/她 nhưng không phải đại từ cần xử lý
MODERN_BLOCKERS = ("小姐", "哥们", "姑娘", "娘娘", "新娘", "娘子")  # chứa 姐/哥/娘 nhưng không phải xưng hô thân tộc
# BR-7.9: 我 khi nói với bề trên.
RESPECT_EXCEPTIONS = (("师尊", "đệ tử"), ("师父", "đệ tử"), ("陛下", "thần"), ("爹", "con"), ("娘", "con"))
RESPECT_MARKERS = ("您", "陛下", "师尊", "前辈", "爹", "娘")

MODERN_MARKERS = {
    "老师": ("em", None), "妈妈": ("con", "mẹ"), "妈": ("con", "mẹ"), "爸爸": ("con", "bố"), "爸": ("con", "bố"),
    "哥哥": ("em", "anh"), "哥": ("em", "anh"), "姐姐": ("em", "chị"), "姐": ("em", "chị"),
    "首长": ("tôi", "thủ trưởng"), "长官": ("tôi", "thủ trưởng"), "司令": ("tôi", "tư lệnh"),
    "师长": ("tôi", "sư trưởng"), "团长": ("tôi", "đoàn trưởng"), "同志": ("tôi", "đồng chí"),
}
ME_MODERN = Rule("我", "", ("tôi", "tớ", "mình", "tao", "em", "con"), "modern.speaker")
YOU_MODERN = Rule("你", "", ("anh", "em", "cậu", "bạn", "mày", "cô", "chị", "ông", "bà"), "modern.listener")
MODERN_CARRY_SEGMENTS = 3

ALL_PHRASES = tuple(
    {f for r in (*NARRATIVE, *DIALOGUE, ME_MODERN, YOU_MODERN) for f in (*r.forms, r.target) if " " in f}
    | {f for r in kinship()[0] for f in (*r.forms, r.target) if " " in f}
)


@lru_cache
def all_rule_forms(modern: bool = False) -> dict[str, set[str]]:
    """Token nguồn -> mọi dạng đích (đích + forms) của luật tương ứng, gộp mọi bảng (kèm cặp xưng hô của từ chỉ vai).

    Token 我/你 lấy luật của lớp đang chạy: DIALOGUE ở lớp xưng hô, ME/YOU_MODERN ở lớp modern."""
    table: dict[str, set[str]] = {}

    def add(token: str, forms) -> None:
        table.setdefault(token, set()).update(f for f in forms if f)

    dialogue = tuple(r for r in DIALOGUE if r.src not in ("我", "你")) + ((ME_MODERN, YOU_MODERN) if modern else tuple(
        r for r in DIALOGUE if r.src in ("我", "你")))
    for r in (*kinship()[0], *NARRATIVE, *dialogue):
        add(r.src, (*r.forms, r.target))
    for marker, pair in MODERN_MARKERS.items():
        add(marker, pair)
    return table


@lru_cache
def all_blockers() -> tuple[str, ...]:
    return tuple(dict.fromkeys((*kinship()[1], *PRONOUN_BLOCKERS, *MODERN_BLOCKERS)))
