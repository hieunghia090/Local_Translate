"""Ước tính token và chi phí DeepSeek (BR-8.24), hiệu chỉnh hệ số theo log (BR-8.24b). Hàm thuần."""
import math
from dataclasses import dataclass

from app.core.lines import count_han

HAN_PER_TOKEN = 1.8
OTHER_PER_TOKEN = 4.0
# Đo thực tế 2026-10-04 (chương 16 của 大宋有种, deepseek-v4-pro): 1.295 token chữ Hán → 4.262 token ra = 3,29 lần.
OUT_RATIO = 3.3
SAFE_OUT_RATIO = OUT_RATIO  # cận dưới của hệ số khi cỡ max_tokens và chia phần, kể cả sau hiệu chỉnh
OUT_HEADROOM = 1.3
CONTEXT_SHARE = 0.6  # BR-8.7
MIN_PART_TOKENS = 256
REVIEW_OUT_SHARE = 0.10
MIN_SAMPLES = 10
WINDOW = 50


@dataclass(frozen=True)
class Coefficients:
    han_per_token: float = HAN_PER_TOKEN
    out_ratio: float = OUT_RATIO
    samples: int = 0

    @property
    def calibrated(self) -> bool:
        return self.samples >= MIN_SAMPLES

    def view(self) -> dict:
        return {"han_per_token": self.han_per_token, "out_ratio": self.out_ratio, "samples": self.samples,
                "calibrated": self.calibrated}


@dataclass(frozen=True)
class Sample:
    """Một dòng log chương: token thật, cùng token văn bản / phần cố định ước tính bằng hệ số mặc định."""

    tokens_in: int
    tokens_out: int
    text_tokens: int
    overhead_tokens: int


def text_tokens(text: str, han_per_token: float = HAN_PER_TOKEN) -> int:
    han = count_han(text)
    other = len(text) - han
    return math.ceil(round(han / han_per_token + other / OTHER_PER_TOKEN, 6))


def prompt_tokens_est(system: str, user: str, coeff: Coefficients) -> int:
    return text_tokens(system, coeff.han_per_token) + text_tokens(user, coeff.han_per_token)


def output_tokens_est(text: str, coeff: Coefficients, share: float = 1.0) -> int:
    return math.ceil(round(text_tokens(text, coeff.han_per_token) * coeff.out_ratio * share, 6))


def part_token_budget(*, context_window: int, max_output: int, fixed_tokens: int, coeff: Coefficients) -> int:
    """BR-8.7: số token văn bản tối đa của một phần (vừa 60% cửa sổ ngữ cảnh và vừa max output)."""
    in_budget = int(context_window * CONTEXT_SHARE) - fixed_tokens
    out_budget = int(max_output / (OUT_HEADROOM * max(coeff.out_ratio, SAFE_OUT_RATIO)))
    return max(MIN_PART_TOKENS, min(in_budget, out_budget))


def review_max_tokens(n_lines: int, max_output: int) -> int:
    """G5: JSON soát cần khoảng 250 token cho mỗi fix, giả định tối đa 30% số dòng có fix."""
    return min(max_output, max(1024, 250 * math.ceil(0.3 * n_lines)))


def calibrate(samples: list[Sample]) -> Coefficients:
    usable = [s for s in samples if s.tokens_in > 0 and s.text_tokens > 0][:WINDOW]
    if len(usable) < MIN_SAMPLES:
        return Coefficients(samples=len(usable))
    scales, ratios = [], []
    for s in usable:
        actual_text = max(1, s.tokens_in - s.overhead_tokens)
        scales.append(actual_text / s.text_tokens)
        ratios.append(s.tokens_out / actual_text)
    scale = sum(scales) / len(scales)
    return Coefficients(han_per_token=round(HAN_PER_TOKEN / scale, 4), out_ratio=round(sum(ratios) / len(ratios), 4),
                        samples=len(usable))


def cost_usd(*, tokens_in: int, tokens_in_cached: int, tokens_out: int, price_in: float, price_cached: float,
             price_out: float) -> float:
    """Giá tính theo 1 triệu token. Phần trúng cache tính theo giá cache."""
    miss = max(0, tokens_in - tokens_in_cached)
    return round((miss * price_in + tokens_in_cached * price_cached + tokens_out * price_out) / 1_000_000, 6)


def error_pct(actual: list[int], est: list[int | None]) -> float | None:
    """Sai số trung bình |thật − ước tính| / thật, tính bằng %."""
    pairs = [(a, e) for a, e in zip(actual, est) if a and e is not None]
    if not pairs:
        return None
    return round(100 * sum(abs(a - e) / a for a, e in pairs) / len(pairs), 1)
