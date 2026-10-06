import httpx
import pytest

from app import db
from app.errors import AppError
from app.main import create_app


def client(app=None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app or create_app()), base_url="http://test")


async def test_health_reports_db_down_with_503(monkeypatch):
    async def fake_ping():
        return False

    monkeypatch.setattr(db, "ping", fake_ping)
    async with client() as c:
        r = await c.get("/api/v1/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "db": "down", "deepseek_key": r.json()["deepseek_key"]}


async def test_health_never_leaks_key(monkeypatch):
    async def fake_ping():
        return True

    monkeypatch.setattr(db, "ping", fake_ping)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secret-value-9999")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        async with client() as c:
            r = await c.get("/api/v1/health")
        assert r.status_code == 200
        assert r.json()["deepseek_key"] is True
        assert "sk-secret" not in r.text and "9999" not in r.text
    finally:
        get_settings.cache_clear()


async def test_unknown_route_uses_error_format():
    async with client() as c:
        r = await c.get("/api/v1/khong-ton-tai")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert set(r.json()["error"]) == {"code", "message", "details"}


async def test_app_error_format():
    app = create_app(frontend_dist=None)

    @app.get("/api/v1/_boom")
    async def boom():
        raise AppError("BOOK_NOT_FOUND", "Không tìm thấy truyện", status=404, details={"id": "x"})

    async with client(app) as c:
        r = await c.get("/api/v1/_boom")
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "BOOK_NOT_FOUND", "message": "Không tìm thấy truyện", "details": {"id": "x"}}}


@pytest.mark.db
async def test_health_ok_with_real_db(migrated_db):
    async with client() as c:
        r = await c.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["db"] == "ok"


async def test_validation_error_from_custom_validator_is_json():
    from pydantic import BaseModel, field_validator

    class Body(BaseModel):
        name: str

        @field_validator("name")
        @classmethod
        def _not_blank(cls, v):
            if not v.strip():
                raise ValueError("Cần nhập tên")
            return v

    app = create_app()

    @app.post("/api/v1/_echo")
    async def echo(body: Body):
        return body

    async with client(app) as c:
        r = await c.post("/api/v1/_echo", json={"name": "  "})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "ctx" not in r.json()["error"]["details"]["errors"][0]
