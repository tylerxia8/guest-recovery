from __future__ import annotations

from app.schema_revision import EXPECTED_SCHEMA_REVISION
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(prefix="/v1/health", tags=["health"])


def schema_matches(version_nums: list[str], expected: str = EXPECTED_SCHEMA_REVISION) -> bool:
    return version_nums == [expected]


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "live"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    engine = request.app.state.engine
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        versions = [str(item) for item in rows]
    except Exception:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "dependency_unavailable",
                    "message": "Database readiness check failed.",
                    "details": {},
                    "request_id": getattr(request.state, "request_id", ""),
                }
            },
        )
    if not schema_matches(versions):
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "schema_mismatch",
                    "message": "Database schema revision does not match this process.",
                    "details": {},
                    "request_id": getattr(request.state, "request_id", ""),
                }
            },
        )
    del settings
    return JSONResponse(
        status_code=200,
        content={"status": "ready", "schema_revision": EXPECTED_SCHEMA_REVISION},
    )
