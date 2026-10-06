import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI

from app.api import ai_glossary, backups, books, chapters, deepseek, events, exports, glossary, hanviet, health, honorific, imports, logs, queue, review
from app.config import get_settings
from app.core.translator import Translator
from app.deepseek.client import DeepSeekClient
from app.db import get_sessionmaker
from app.errors import install_error_handlers
from app.services import exports as export_service
from app.services import hanviet as hanviet_service
from app.services import honorific as honorific_service
from app.services import logs as log_service
from app.services.events import EventBroker
from app.services.translators import default_translator_factory
from app.web import FRONTEND_DIST, mount_frontend

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with get_sessionmaker()() as s:
            await log_service.ensure_partitions(s)
            await honorific_service.fail_stale_reapply_jobs(s)
            await hanviet_service.ensure_seed(s)  # nạp data/hanviet_seed.tsv lần đầu
            await export_service.fail_stale_exports(s)
            await s.commit()
        await anyio.to_thread.run_sync(export_service.remove_partial_files)
    except Exception:  # DB chưa chạy: /health sẽ báo, không chặn server khởi động
        log.warning("Không tạo được partition log lúc khởi động", exc_info=True)
    yield
    await app.state.broker.close()


def create_app(
    translator_factory: Callable[[], Translator] | None = None,
    frontend_dist: Path | None = FRONTEND_DIST,
    deepseek_client_factory: Callable[[], DeepSeekClient] | None = None,
    hanviet_autofill: bool | None = None,
) -> FastAPI:
    app = FastAPI(title="Local Translate", version="0.1.0", lifespan=lifespan)
    app.state.translator_factory = translator_factory or default_translator_factory
    app.state.deepseek_client_factory = deepseek_client_factory or DeepSeekClient.from_settings
    app.state.hanviet_autofill = get_settings().hanviet_autofill if hanviet_autofill is None else hanviet_autofill
    app.state.broker = EventBroker()
    install_error_handlers(app)
    for module in (health, imports, books, chapters, ai_glossary, glossary, hanviet, honorific, logs, queue, review, deepseek, exports, backups, events):
        app.include_router(module.router, prefix="/api/v1")
    mount_frontend(app, frontend_dist)
    return app


app = create_app()
