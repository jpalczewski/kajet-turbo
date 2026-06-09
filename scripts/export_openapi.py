#!/usr/bin/env python3
"""Export FastAPI REST API schema to openapi.json without starting the server.

kajet_turbo/dependencies.py initializes Database() at module import time.
DB_PATH must be set before importing any kajet_turbo module to avoid
attempting to write to /data/kajet.db (Docker path, may not exist locally).
"""
import json
import os
from pathlib import Path

os.environ.setdefault("DB_PATH", "/tmp/kajet_openapi_export.db")
os.environ.setdefault("MCP_BASE_URL", "http://localhost:8000")

from fastapi import FastAPI  # noqa: E402
from kajet_turbo.api import api_router  # noqa: E402


def build_schema_app() -> FastAPI:
    app = FastAPI(title="Kajet Turbo API", version="0.1.0")
    app.include_router(api_router)
    return app


def main() -> None:
    app = build_schema_app()
    schema = app.openapi()
    output = Path(__file__).parent.parent / "openapi.json"
    output.write_text(json.dumps(schema, indent=2))
    print(f"Written: {output} ({len(schema['paths'])} paths)")


if __name__ == "__main__":
    main()
