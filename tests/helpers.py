"""Helpers shared across the root test suite.

Log assertions are the common case here: our sink writes JSONL to stderr, so every
test that checks a log line has to capture, split and parse it.
"""

import json
from typing import Any


def vec_identity(dim: int, model: str = "test-model", backend: str = "http://test"):
    """An IndexIdentity for tests that only care about the vector-table shard.

    Pass a distinct ``model`` (or ``backend``) when the test is about two vector spaces
    coexisting at the same dimension.
    """
    from kajet_turbo.embedding.identity import IndexIdentity

    return IndexIdentity(backend=backend, model=model, dim=dim)


def read_log_entries(capsys) -> list[dict[str, Any]]:
    """Parse the JSONL our sink wrote to stderr.

    ``capsys.readouterr()`` drains the buffer, so call this once per assertion block
    and keep the list — a second call returns only what was logged after the first.
    """
    captured = capsys.readouterr()
    return [json.loads(line) for line in captured.err.strip().split("\n") if line]


def entries_named(entries: list[dict[str, Any]], msg: str) -> list[dict[str, Any]]:
    """Entries whose ``msg`` field equals ``msg`` (e.g. "http", "ws_connected")."""
    return [entry for entry in entries if entry.get("msg") == msg]


def make_logging_app():
    """A FastAPI app wrapped in LoggingMiddleware, with logging set up.

    Register routes on the result. Imported late so the env block in conftest.py runs
    before kajet_turbo — see tests/CLAUDE.md.
    """
    from fastapi import FastAPI

    from kajet_turbo.log import LoggingMiddleware, setup_logging

    setup_logging()
    app = FastAPI()
    app.add_middleware(LoggingMiddleware)
    return app
