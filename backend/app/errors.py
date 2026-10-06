import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)
_HTTP_CODES = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 401: "UNAUTHORIZED", 403: "FORBIDDEN"}


_RETRYABLE_SQLSTATES = {"40P01", "55P03"}  # deadlock_detected, lock_not_available


def _sqlstate(exc: DBAPIError) -> str | None:
    """asyncpg để sqlstate ở `orig` hoặc ở `orig.__cause__` tuỳ lớp bọc của SQLAlchemy."""
    orig = exc.orig
    for err in (orig, getattr(orig, "__cause__", None)):
        code = getattr(err, "sqlstate", None) or getattr(err, "pgcode", None)
        if code:
            return code
    return None


class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details or {}


def _body(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def _clean_errors(errors) -> list[dict]:
    """Bỏ `ctx` (có thể chứa object exception) để lỗi luôn chuyển được sang JSON."""
    return jsonable_encoder([{k: v for k, v in e.items() if k != "ctx"} for e in errors])


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(
            _body("VALIDATION_ERROR", "Dữ liệu gửi lên không hợp lệ", {"errors": _clean_errors(exc.errors())}),
            status_code=422,
        )

    @app.exception_handler(DBAPIError)
    async def _db_error(request: Request, exc: DBAPIError):
        if _sqlstate(exc) in _RETRYABLE_SQLSTATES:
            return JSONResponse(
                _body("CONFLICT_RETRY", "Thao tác đang tranh chấp với một thao tác khác, hãy thử lại"),
                status_code=409,
            )
        return await _unexpected(request, exc)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("Lỗi không mong muốn", exc_info=exc)
        return JSONResponse(_body("INTERNAL_ERROR", "Lỗi không mong muốn ở server"), status_code=500)
