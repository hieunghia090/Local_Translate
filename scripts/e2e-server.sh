#!/usr/bin/env bash
# Server cho Playwright: DB test sạch, DATA_DIR tạm, API phục vụ frontend/dist ở cổng 8765, worker thật.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
cd "$ROOT/backend"
export DATABASE_URL="$("$PY" -c 'from app.config import get_settings; print(get_settings().test_database_url)')"
export DATA_DIR="$(mktemp -d)/lt"
export HF_HUB_OFFLINE="${HF_HUB_OFFLINE:-1}"
export DEEPSEEK_API_KEY=""  # khối AI ở Tạo truyện bật sẵn: e2e không được dùng key thật trong .env
"$PY" -m alembic upgrade head
"$PY" - <<'PYEOF'
import asyncio
from sqlalchemy import text
from app.db import get_engine

async def main():
    async with get_engine().begin() as conn:
        await conn.execute(text("TRUNCATE books, import_sessions, log_entries CASCADE"))
        await conn.execute(text("UPDATE worker_state SET paused = false"))
    await get_engine().dispose()

asyncio.run(main())
PYEOF
# NFR-2: 50 truyện, một truyện 2.000 chương (e2e/perf.spec.ts). Đặt E2E_SEED_PERF=0 để bỏ qua.
if [ "${E2E_SEED_PERF:-1}" = "1" ]; then
  "$PY" -m app.devtools.seed_perf >/dev/null
fi
# Spec 06 mục 4a: một chương có đủ bản Hachimi và bản AI (e2e/compare.spec.ts). Đặt E2E_SEED_COMPARE=0 để bỏ qua.
if [ "${E2E_SEED_COMPARE:-1}" = "1" ]; then
  "$PY" -m app.devtools.seed_compare >/dev/null
fi
trap 'kill 0' EXIT INT TERM
"$PY" -m app.worker &
"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --log-level warning &
wait
