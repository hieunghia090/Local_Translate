#!/usr/bin/env bash
# Chạy app như bản dùng thật: build frontend, API phục vụ giao diện ở 127.0.0.1:8000, cùng worker.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
cd "$ROOT"
docker compose up -d db
(cd backend && "$PY" -m alembic upgrade head)
if [ ! -f frontend/dist/index.html ] || [ -n "$(find frontend/src frontend/index.html -newer frontend/dist/index.html -print -quit)" ]; then
  (cd frontend && npm run build)
fi
trap 'kill 0' EXIT INT TERM
(cd backend && "$PY" -m app.worker) &
(cd backend && "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000) &
echo "Mở http://127.0.0.1:8000"
wait
