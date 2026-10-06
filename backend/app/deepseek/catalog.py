"""Model DeepSeek mặc định và giá mẫu (USD / 1 triệu token). Giá là MẪU: người dùng sửa ở Cấu hình → Chi phí (BR-8.25)."""

DEEPSEEK_MODELS = ("deepseek-v4-pro", "deepseek-flash")
FALLBACK_CONTEXT_WINDOW = 65536  # khi model không có trong bảng ai_models
FALLBACK_MAX_OUTPUT = 8192

DEFAULT_MODELS: list[dict] = [
    {"id": "deepseek-v4-pro", "provider": "deepseek", "label": "DeepSeek V4 Pro", "context_window": 131072,
     "max_output_tokens": 8192, "price_in_per_mtok": 0.27, "price_in_cached_per_mtok": 0.07,
     "price_out_per_mtok": 1.10, "prices_are_samples": True, "enabled": True},
    {"id": "deepseek-flash", "provider": "deepseek", "label": "DeepSeek Flash", "context_window": 131072,
     "max_output_tokens": 8192, "price_in_per_mtok": 0.07, "price_in_cached_per_mtok": 0.02,
     "price_out_per_mtok": 0.28, "prices_are_samples": True, "enabled": True},
]


def is_deepseek_model(model_id: str | None) -> bool:
    return bool(model_id) and model_id.startswith("deepseek")
