from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]  # thư mục gốc repo (chứa .env)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True
    )

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_default_model: str = "deepseek-v4-pro"
    hanviet_autofill: bool = True  # bổ sung âm Hán Việt nền bằng DeepSeek (spec 04 mục 6a bước 3)
    database_url: str = "postgresql://local_translate:change-me@127.0.0.1:5432/local_translate"
    test_database_url_override: str = Field(default="", alias="TEST_DATABASE_URL")
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    data_dir: Path = Path("~/LocalTranslate")
    ct2_intra_threads: int = Field(default=8, ge=1, le=32, alias="CT2_INTRA_THREADS")  # đo trên M5 (4P+6E): 8 nhanh nhất
    ct2_inter_threads: int = Field(default=1, ge=1, le=8, alias="CT2_INTER_THREADS")
    postgres_port: int = 5432  # cổng docker compose mở trên máy (POSTGRES_PORT trong .env)

    @property
    def async_database_url(self) -> str:
        return _to_asyncpg(self.database_url)

    @property
    def test_database_url(self) -> str:
        if self.test_database_url_override:
            return self.test_database_url_override
        base, _, _db = self.database_url.rpartition("/")
        return f"{base}/local_translate_test"

    @property
    def data_path(self) -> Path:
        return self.data_dir.expanduser()

    def masked_deepseek_key(self) -> str | None:
        key = self.deepseek_api_key.strip()
        return f"••••{key[-4:]}" if key else None


def _to_asyncpg(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    return url.replace("postgresql://", "postgresql+asyncpg://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
