"""Machine-readable error codes layered on FastAPI's default JSON error shape.

`detail` keeps carrying the exact message every existing caller already reads
(the UI, the CLI's HTTP client, and every test asserting on `.json()["detail"]`)
-- this module is additive, not a breaking reshape. `code` is new: a stable,
English, machine-parseable string an automation script can branch on without
regex-matching a Vietnamese sentence.

Call `install(app)` once, right after the `FastAPI()` app is constructed.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# Default code derived from the status code alone, for the many call sites
# that raise plain `HTTPException` and have nothing more specific to say.
_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "unauthorized",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
}
_DEFAULT_CODE = "internal_error"


class CodedHTTPException(HTTPException):
    """An `HTTPException` carrying an explicit machine-readable `code`.

    Raise this instead of plain `HTTPException` wherever the status-derived
    default (see `_STATUS_CODES`) is not specific enough for an automation
    caller to act on -- e.g. `revise_failed`, `model_refused`,
    `source_unreachable`, `idempotency_conflict`.
    """

    def __init__(self, status_code: int, detail: Any = None, *,
                 code: str | None = None, headers: dict[str, str] | None = None):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code or _STATUS_CODES.get(status_code, _DEFAULT_CODE)


def _code_for(exc: StarletteHTTPException) -> str:
    explicit = getattr(exc, "code", None)
    if explicit:
        return str(explicit)
    return _STATUS_CODES.get(exc.status_code, _DEFAULT_CODE)


def install(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": _code_for(exc)},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        _: Request, exc: RequestValidationError,
    ) -> JSONResponse:
        # `.errors()` is exactly what FastAPI's own default handler puts under
        # `detail` -- unchanged shape, `code` only added.
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors(), "code": "validation_error"},
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"detail": f"Lỗi máy chủ: {exc}", "code": _DEFAULT_CODE},
        )
