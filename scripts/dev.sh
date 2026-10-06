#!/usr/bin/env bash
# Chạy toàn bộ hệ thống để phát triển: Postgres, API (tự nạp lại), worker, Vite.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
cd "$ROOT"
docker compose up -d db
(cd backend && "$PY" -m alembic upgrade head)
trap 'kill 0' EXIT INT TERM
(cd backend && "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload) &
(cd backend && "$PY" -m app.worker) &
(cd frontend && npm run dev) &
echo "Mở http://127.0.0.1:5173 (Vite) — API ở http://127.0.0.1:8000"
wait
