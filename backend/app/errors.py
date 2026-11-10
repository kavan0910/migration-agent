"""API errors in the contract shape: {"error": {"code", "message"}}."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}
        super().__init__(message)


def not_found(what: str = "resource") -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def forbidden(message: str = "insufficient role") -> ApiError:
    return ApiError(403, "forbidden", message)


def _body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
          409: "conflict", 413: "payload_too_large", 422: "invalid_request", 429: "rate_limited"}


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError):
        return JSONResponse(_body(e.code, e.message), status_code=e.status, headers=e.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException):
        return JSONResponse(_body(_CODES.get(e.status_code, "error"), str(e.detail)), status_code=e.status_code,
                            headers=getattr(e, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError):
        parts = [f"{'.'.join(str(x) for x in err['loc'] if x != 'body')}: {err['msg']}" for err in e.errors()[:5]]
        return JSONResponse(_body("invalid_request", "; ".join(parts)), status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, e: Exception):
        import logging
        logging.getLogger("app.error").exception("unhandled %s", type(e).__name__)
        return JSONResponse(_body("internal_error", "internal server error"), status_code=500)
