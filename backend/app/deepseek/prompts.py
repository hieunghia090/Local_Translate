"""Prompt cho DeepSeek. System (prompt nền + khối Xưng hô) ổn định theo truyện để trúng cache tiền tố (BR-8.1).
Mọi thứ thay đổi theo chương nằm ở user prompt."""
from dataclasses import dataclass

from app.deepseek.estimate import HAN_PER_TOKEN, text_tokens

CONTEXT_MAX_CHARS = 600  # BR-8.4
ANCIENT_GENRES = ("xianxia", "xuanhuan")
MODERN_GENRES = ("urban", "modern_war", "romance")

DEFAULT_FOUNDATION = """Bạn là dịch giả chuyên nghiệp, dịch tiểu thuyết mạng từ tiếng Trung sang tiếng Việt.

# NHIỆM VỤ
- Dịch đủ từng dòng, đúng nghĩa. Không tóm tắt, không bỏ câu, không thêm lời bình hay giải thích.
- Mỗi dòng đầu vào có dạng "⟦số⟧ câu gốc". Trả về đúng một dòng "⟦số⟧ câu dịch" cho mỗi dòng, giữ nguyên số.

# THỨ TỰ ƯU TIÊN
1. Glossary: thuật ngữ ở mục "THUẬT NGỮ" phải dùng đúng bản dịch đã cho.
2. Giọng văn: giữ không khí và giọng kể của truyện, nối mạch với đoạn ngữ cảnh chương trước.
3. Tự nhiên: câu tiếng Việt trôi chảy, đúng ngữ pháp, không dịch từng chữ.

# PHIÊN ÂM TÊN RIÊNG
- Tên người, địa danh, môn phái Trung Quốc: dùng âm Hán Việt, viết hoa mỗi chữ (林凡 → Lâm Phàm).
- Tên Nga / châu Âu viết bằng chữ Hán: khôi phục tên gốc theo cách viết Latin quen dùng (赫鲁晓夫 → Khrushchev).
- Tên Nhật: dùng Romaji Hepburn (田中一郎 → Tanaka Ichirō).

# ĐỊNH DẠNG ĐẦU RA
- Chỉ trả các dòng "⟦số⟧ câu dịch", theo đúng thứ tự đầu vào.
- Không viết lời mở đầu, không dùng markdown, không chép lại câu gốc.
- Mục "NGỮ CẢNH" chỉ để tham khảo, không dịch lại.
- Làm theo mục "GHI CHÚ SỬA" nếu có."""

REVIEW_SYSTEM = """Bạn là biên tập viên soát bản dịch tiểu thuyết Trung → Việt.
Mỗi cặp gồm dòng "⟦số⟧ câu gốc" và dòng "⟹ bản dịch" ngay dưới.
Chỉ báo những dòng THỰC SỰ cần sửa, theo đúng một trong các loại:
- name_mismatch: tên riêng / thuật ngữ sai so với mục THUẬT NGỮ;
- missing_content: bản dịch bỏ sót ý;
- mistranslation: dịch sai nghĩa;
- honorific: sai xưng hô;
- grammar: sai ngữ pháp nặng.
Không đề xuất sửa dòng có đánh dấu [KHÔNG SỬA]; các dòng đó chỉ là ngữ cảnh.
Trả về đúng một đối tượng JSON, không kèm chữ nào khác:
{"fixes": [{"idx": số, "type": "name_mismatch|missing_content|mistranslation|honorific|grammar", "before": "bản dịch hiện tại, chép nguyên văn", "after": "cả câu dịch đã sửa", "reason": "lý do ngắn", "confidence": 0-100}]}
Không có gì cần sửa thì trả {"fixes": []}."""


def honorific_block(genre: str, honorific: dict) -> str:
    """Khối Xưng hô sinh từ thể loại và cấu hình honorific (BR-8.2). Chỉ phụ thuộc dữ liệu của truyện."""
    lines = ["# XƯNG HÔ"]
    ancient = genre in ANCIENT_GENRES
    if ancient:
        lines.append("- Truyện cổ trang: lời kể dùng hắn / nàng / y; lời thoại dùng ta / ngươi / các ngươi, "
                     "không dùng tôi / bạn / anh ấy / cô ấy.")
    elif genre in MODERN_GENRES:
        lines.append("- Truyện hiện đại: dùng tôi / anh / em / cậu / cô / anh ấy / cô ấy theo tuổi tác và quan hệ nhân vật.")
    else:
        lines.append("- Chọn đại từ theo bối cảnh của truyện, giữ nhất quán giữa các chương.")
    if honorific.get("pronoun") and not ancient:
        lines.append("- Đoạn mang không khí cổ trang: dùng ta / ngươi / hắn / nàng.")
    if honorific.get("kinship"):
        lines.append("- Xưng hô sư môn và thân tộc theo Hán Việt: 师兄 → sư huynh, 师姐 → sư tỷ, 师弟 → sư đệ, "
                     "师妹 → sư muội, 师父 → sư phụ, 哥哥 → ca ca, 姐姐 → tỷ tỷ.")
    if honorific.get("modern_stable"):
        lines.append("- Giữ cố định cách hai nhân vật xưng hô với nhau qua mọi chương, trừ khi quan hệ của họ thay đổi.")
    return "\n".join(lines)


def system_prompt(foundation: str | None, genre: str, honorific: dict) -> str:
    base = (foundation or DEFAULT_FOUNDATION).strip()
    return f"{base}\n\n{honorific_block(genre, honorific)}"


def tail_context(lines: list[str], max_chars: int = CONTEXT_MAX_CHARS) -> str | None:
    """Đoạn cuối bản dịch chương trước: các dòng cuối, tổng không quá max_chars ký tự."""
    picked: list[str] = []
    used = 0
    for line in reversed([x.strip() for x in lines if x and x.strip()]):
        extra = len(line) + (1 if picked else 0)
        if used + extra > max_chars:
            if not picked:
                picked.append(line[-max_chars:])
            break
        picked.append(line)
        used += extra
    return "\n".join(reversed(picked)) or None


def user_prompt(*, glossary_lines, context: str | None, notes: list[str], lines: list[tuple[int, str]]) -> str:
    parts = []
    if glossary_lines:
        parts.append("# THUẬT NGỮ (bắt buộc dùng đúng)\n" + "\n".join(glossary_lines))
    if context:
        parts.append("# NGỮ CẢNH — đoạn cuối chương trước (không dịch lại)\n" + context)
    if notes:
        parts.append("# GHI CHÚ SỬA TỪ LẦN DỊCH TRƯỚC\n" + "\n".join(f"- [correction] {n}" for n in notes))
    parts.append("# VĂN BẢN CẦN DỊCH\n" + "\n".join(f"⟦{i}⟧ {t}" for i, t in lines))
    return "\n\n".join(parts)


@dataclass(frozen=True)
class ReviewItem:
    idx: int
    src: str
    dst: str
    locked: bool  # câu đã sửa tay: chỉ làm ngữ cảnh (BR-8.22)


def review_user_prompt(glossary_lines, items: list[ReviewItem]) -> str:
    parts = []
    if glossary_lines:
        parts.append("# THUẬT NGỮ\n" + "\n".join(glossary_lines))
    body = []
    for it in items:
        body.append(f"⟦{it.idx}⟧ {'[KHÔNG SỬA] ' if it.locked else ''}{it.src}")
        body.append(f"⟹ {it.dst}")
    parts.append("# BẢN GỐC VÀ BẢN DỊCH\n" + "\n".join(body))
    return "\n\n".join(parts)


def split_lines(lines: list[tuple[int, str]], max_tokens: int,
                han_per_token: float = HAN_PER_TOKEN) -> list[list[tuple[int, str]]]:
    """Chia các dòng thành phần, mỗi phần tối đa max_tokens token (BR-8.7). Một dòng quá dài đứng riêng một phần."""
    parts: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    used = 0
    for idx, text in lines:
        cost = text_tokens(f"⟦{idx}⟧ {text}", han_per_token) + 1
        if current and used + cost > max_tokens:
            parts.append(current)
            current, used = [], 0
        current.append((idx, text))
        used += cost
    if current:
        parts.append(current)
    return parts
