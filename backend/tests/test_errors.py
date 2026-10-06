import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import DBAPIError

from app.errors import install_error_handlers


class _Orig(Exception):
    def __init__(self, sqlstate=None, cause_sqlstate=None):
        super().__init__("lỗi giả")
        if sqlstate:
            self.sqlstate = sqlstate
        if cause_sqlstate:
            self.__cause__ = type("Cause", (Exception,), {"sqlstate": cause_sqlstate})()


def _app(orig) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise DBAPIError("SELECT 1", {}, orig)

    return app


async def _get(orig):
    transport = httpx.ASGITransport(app=_app(orig), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/boom")


@pytest.mark.parametrize(
    "orig",
    [_Orig("40P01"), _Orig("55P03"), _Orig(cause_sqlstate="40P01"), _Orig(cause_sqlstate="55P03")],
)
async def test_deadlock_and_lock_timeout_return_409_retry(orig):
    r = await _get(orig)
    assert r.status_code == 409
    assert r.json() == {
        "error": {
            "code": "CONFLICT_RETRY",
            "message": "Thao tác đang tranh chấp với một thao tác khác, hãy thử lại",
            "details": {},
        }
    }


@pytest.mark.parametrize("orig", [_Orig("23505"), _Orig()])
async def test_other_db_errors_fall_through_to_500(orig):
    r = await _get(orig)
    assert r.status_code == 500 and r.json()["error"]["code"] == "INTERNAL_ERROR"
