PY := "$(CURDIR)/.venv/bin/python"

.PHONY: db migrate test test-db test-slow hanviet test-live api worker fe-install fe-build fe-test dev start e2e backup seed-perf

db:
	docker compose up -d db

migrate:
	cd backend && $(PY) -m alembic upgrade head

test:
	cd backend && $(PY) -m pytest

test-db:
	cd backend && $(PY) -m pytest -m "db and not live"

test-slow:
	cd backend && $(PY) -m pytest -m slow -s

hanviet:
	cd backend && $(PY) -m app.hanviet_build $(ARGS)

test-live:
	cd backend && LT_LIVE=1 $(PY) -m pytest -m live -s

api:
	cd backend && $(PY) -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

worker:
	cd backend && $(PY) -m app.worker

fe-install:
	cd frontend && npm install

fe-build:
	cd frontend && npm run build

fe-test:
	cd frontend && npm test

dev:
	bash scripts/dev.sh

start:
	bash scripts/start.sh

e2e:
	cd frontend && npx playwright install chromium && npm run e2e

backup:
	cd backend && $(PY) -m app.services.backup

seed-perf:
	cd backend && $(PY) -m app.devtools.seed_perf $(ARGS)
