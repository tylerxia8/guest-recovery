from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.errors import AppError

logger = logging.getLogger("app.api")


def request_id_from(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str):
        return existing
    return str(uuid.uuid4())


def error_body(
    code: str, message: str, request_id: str, details: Mapping[str, object] | None = None
) -> dict[str, object]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": dict(details or {}),
            "request_id": request_id,
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, request_id_from(_request), exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        malformed = any(item.get("type") == "json_invalid" for item in errors)
        status = 400 if malformed else 422
        code = "malformed_json" if malformed else "validation_error"
        details = {
            "errors": [
                {
                    "loc": [str(part) for part in item.get("loc", [])],
                    "msg": str(item.get("msg", "invalid")),
                    "type": str(item.get("type", "value_error")),
                }
                for item in errors
            ]
        }
        return JSONResponse(
            status_code=status,
            content=error_body(code, "Request validation failed.", request_id_from(request), details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "http_error"
        if exc.status_code == 404:
            code = "not_found"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, "Request failed.", request_id_from(request)),
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled %s request_id=%s", type(exc).__name__, request_id_from(request))
        return JSONResponse(
            status_code=500,
            content=error_body(
                "internal_error",
                "The request could not be completed.",
                request_id_from(request),
            ),
        )
