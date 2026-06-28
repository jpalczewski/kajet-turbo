from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from kajet_turbo.concurrency import run_sync


def _response(status_code: int, content: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=content,
        headers={"Cache-Control": "no-store"},
    )


def _db_ping(engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def add_health_routes(app: FastAPI, *, engine) -> None:
    async def healthz() -> JSONResponse:
        return _response(200, {"status": "ok"})

    async def readyz() -> JSONResponse:
        try:
            await run_sync(_db_ping, engine)
        except Exception:
            return _response(503, {"status": "error", "checks": {"db": "error"}})
        return _response(200, {"status": "ok", "checks": {"db": "ok"}})

    app.add_api_route("/healthz", healthz, methods=["GET"], include_in_schema=False)
    app.add_api_route("/readyz", readyz, methods=["GET"], include_in_schema=False)
