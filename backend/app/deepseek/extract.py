"""AI trích thuật ngữ (spec 04 mục 6): prompt cố định, chia lô, cứu JSON bị cắt, lọc bằng code, gộp lô. Hàm thuần."""
import json
import re
from collections import Counter
from dataclasses import dataclass, replace

from app.core.glossary import nfc
from app.deepseek.client import OutputRejected

CATEGORY_CODES = ("character", "location", "organization", "term", "rank", "realm", "item", "abbreviation")
DEFAULT_CATEGORIES = ("character", "organization", "realm", "location")
NAME_LANGS = ("zh", "ja", "foreign")
BATCH_CHARS = 12_000
MIN_OCCURRENCES = 2
TEMPERATURE = 0.1
OUT_SHARE = 0.25  # ước tính token ra / token văn bản của lô
CONTEXT_WIDTH = 24
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_SRC = re.compile(r"^[㐀-䶿一-鿿豈-﫿0-9·]+$")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")

EXTRACT_SYSTEM = """Bạn là biên tập viên lập glossary cho bản dịch tiểu thuyết mạng Trung → Việt.
Đọc văn bản ở mục "VĂN BẢN" và liệt kê tên riêng, thuật ngữ cần dịch thống nhất trong cả truyện.
Chỉ trích các loại có trong mục "LOẠI CẦN TRÍCH", dùng đúng mã loại:
- character: nhân vật
- location: địa danh
- organization: tổ chức / môn phái
- term: thuật ngữ
- rank: quân hàm / chức vụ
- realm: cảnh giới
- item: vật phẩm / công pháp
- abbreviation: viết tắt
Quy tắc cho suggested_target:
- Tên người, địa danh Trung Quốc: âm Hán Việt, viết hoa mỗi chữ (毛泽东 → Mao Trạch Đông), name_lang = "zh".
- Tên Nga / châu Âu viết bằng chữ Hán: khôi phục tên gốc (斯大林 → Stalin), name_lang = "foreign".
- Địa danh quốc tế: tên tiếng Việt thông dụng (朝鲜 → Triều Tiên).
- Tên Nhật: Romaji Hepburn (田中一郎 → Tanaka Ichirō), name_lang = "ja".
- Thuật ngữ và tổ chức: dịch nghĩa.
- Không bao giờ để nguyên chữ Hán trong suggested_target.
source_term chép đúng nguyên văn chữ Hán trong văn bản. confidence là số nguyên 0–100.
Trả về đúng một đối tượng JSON, không kèm chữ nào khác:
{"terms": [{"type": "character", "source_term": "赵楷", "suggested_target": "Triệu Khải", "name_lang": "zh", "confidence": 95, "notes": ""}]}
Không có gì thì trả {"terms": []}."""


def extract_user_prompt(text: str, categories) -> str:
    """Loại cần trích nằm ở user prompt để system giống hệt nhau mọi lần (cache tiền tố)."""
    return "# LOẠI CẦN TRÍCH\n" + ", ".join(categories) + "\n\n# VĂN BẢN\n" + text.strip()


@dataclass(frozen=True)
class ChapterText:
    chapter_id: object  # uuid của chương, None khi chạy thử trên ImportSession
    no: int
    text: str


@dataclass(frozen=True)
class Batch:
    text: str
    chapter_ids: tuple
    first_no: int
    last_no: int


def make_batches(chapters: list[ChapterText], max_chars: int = BATCH_CHARS) -> list[Batch]:
    """Ghép các dòng có chữ Hán theo thứ tự chương tới ~max_chars ký tự. Dòng meta (===, Nguồn, URL) không gửi.
    Một dòng dài hơn max_chars thì cắt cứng. Chương không có chữ Hán không sinh lô nào."""
    batches: list[Batch] = []
    cur: list[str] = []
    ids: list = []
    nos: list[int] = []
    size = 0

    def flush() -> None:
        nonlocal cur, ids, nos, size
        if cur:
            batches.append(Batch("\n".join(cur), tuple(dict.fromkeys(ids)), nos[0], nos[-1]))
        cur, ids, nos, size = [], [], [], 0

    for ch in chapters:
        for raw in ch.text.split("\n"):
            line = raw.strip()
            if not line or not _HAN.search(line):
                continue
            while line:
                piece, line = line[:max_chars], line[max_chars:]
                if cur and size + len(piece) + 1 > max_chars:
                    flush()
                cur.append(piece)
                ids.append(ch.chapter_id)
                nos.append(ch.no)
                size += len(piece) + 1
    flush()
    return batches


def context_snippet(text: str, src: str, width: int = CONTEXT_WIDTH) -> str:
    i = text.find(src)
    if i < 0:
        return ""
    return text[max(0, i - width): i + len(src) + width].replace("\n", " ").strip()


def _objects_in_array(text: str, start: int) -> list[dict]:
    """Các object JSON đầy đủ trong mảng bắt đầu ở `start` (bỏ object cuối bị cắt dở). Tôn trọng chuỗi và escape."""
    out: list[dict] = []
    depth, in_str, esc, obj_start = 0, False, False, None
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    value = json.loads(text[obj_start: i + 1])
                except ValueError:
                    value = None
                if isinstance(value, dict):
                    out.append(value)
                obj_start = None
        elif c == "]" and depth == 0:
            break
    return out


def salvage_terms(text: str) -> list[dict]:
    key = text.find('"terms"')
    start = text.find("[", key if key >= 0 else 0)
    return [] if start < 0 else _objects_in_array(text, start + 1)


def parse_terms(content: str) -> tuple[list, bool]:
    """Mục 6 bước 3: (các phần tử, có phải cứu từ output bị cắt). Không đọc được gì thì ném OutputRejected (thử lại)."""
    raw = _FENCE.sub("", content.strip())
    try:
        data = json.loads(raw)
    except ValueError:
        items = salvage_terms(raw)
        if not items:
            raise OutputRejected("json", "JSON trích glossary không hợp lệ") from None
        return items, True
    if isinstance(data, list):
        return data, False
    if isinstance(data, dict) and isinstance(data.get("terms"), list):
        return data["terms"], False
    raise OutputRejected("json", "JSON trích glossary thiếu mảng terms")


@dataclass(frozen=True)
class Proposal:
    src: str
    dst: str
    category: str
    name_lang: str | None
    confidence: int
    notes: str
    context: str


@dataclass
class Drops:
    invalid: int = 0
    existing: int = 0
    han_target: int = 0
    low_freq: int = 0

    def add(self, other: "Drops") -> None:
        self.invalid += other.invalid
        self.existing += other.existing
        self.han_target += other.han_target
        self.low_freq += other.low_freq

    def view(self) -> dict:
        return {"dropped_invalid": self.invalid, "dropped_existing": self.existing,
                "dropped_han_target": self.han_target, "dropped_low_freq": self.low_freq}


def _validate(item, categories) -> Proposal | None:
    if not isinstance(item, dict):
        return None
    src, dst, typ = item.get("source_term"), item.get("suggested_target"), item.get("type")
    if not all(isinstance(v, str) for v in (src, dst, typ)):
        return None
    src, dst = nfc(src.strip()), nfc(" ".join(dst.split()))
    if not src or not dst or not _SRC.match(src) or len(src) > 100 or len(dst) > 200:
        return None
    if typ not in CATEGORY_CODES or typ not in categories:
        return None
    lang = item.get("name_lang") or None
    if lang not in (None, *NAME_LANGS):
        return None
    try:
        conf = max(0, min(100, int(round(float(item.get("confidence", 50))))))
    except (TypeError, ValueError):
        return None
    notes = item.get("notes")
    return Proposal(src, dst, typ, lang, conf, notes.strip()[:500] if isinstance(notes, str) else "", "")


def filter_proposals(raw: list, batch_text: str, *, known: set[str], rejected: set[str],
                     categories) -> tuple[list[Proposal], Drops]:
    """Mục 6 bước 4, đúng thứ tự: sai schema / loại lạ → đã có / đã từ chối / trùng lô → còn chữ Hán → < 2 lần."""
    drops = Drops()
    kept: list[Proposal] = []
    seen: set[str] = set()
    for item in raw:
        p = _validate(item, categories)
        if p is None:
            drops.invalid += 1
            continue
        if p.src in known or p.src in rejected or p.src in seen:
            drops.existing += 1
            continue
        seen.add(p.src)
        if _HAN.search(p.dst):
            drops.han_target += 1
            continue
        if batch_text.count(p.src) < MIN_OCCURRENCES:
            drops.low_freq += 1
            continue
        kept.append(replace(p, context=context_snippet(batch_text, p.src)))
    return kept, drops


def _most_common(values: list):
    counts = Counter(values)
    best = max(counts.values())
    return next(v for v in values if counts[v] == best)


def merge_proposals(groups: list[list[Proposal]]) -> list[Proposal]:
    """Mục 6 bước 5: cùng source_term thì giữ dst nhiều nhất, confidence trung bình."""
    by_src: dict[str, list[Proposal]] = {}
    for group in groups:
        for p in group:
            by_src.setdefault(p.src, []).append(p)
    out: list[Proposal] = []
    for src, ps in by_src.items():
        out.append(Proposal(
            src, _most_common([p.dst for p in ps]), _most_common([p.category for p in ps]),
            _most_common([p.name_lang for p in ps]), round(sum(p.confidence for p in ps) / len(ps)),
            next((p.notes for p in ps if p.notes), ""), next((p.context for p in ps if p.context), ""),
        ))
    return out
