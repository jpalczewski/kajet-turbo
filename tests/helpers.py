"""Helpers shared across the root test suite.

Log assertions are the common case here: our sink writes JSONL to stderr, so every
test that checks a log line has to capture, split and parse it.
"""

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kajet_turbo.repositories.notes import RelatedNotesResult


def vec_identity(dim: int, model: str = "test-model", backend: str = "http://test"):
    """An IndexIdentity for tests that only care about the vector-table shard.

    Pass a distinct ``model`` (or ``backend``) when the test is about two vector spaces
    coexisting at the same dimension.
    """
    from kajet_turbo.embedding.identity import IndexIdentity

    return IndexIdentity(backend=backend, model=model, dim=dim)


def related_note(note_id: str, owner_id: str = "u1", workspace: str = "ws", folder: str = ""):
    """A minimal Note row for related-notes tests, seeded directly — these tests only
    need rows and chunks to exist, not a full git-backed write via the note services."""
    from kajet_turbo.models import Note

    return Note(
        id=note_id,
        workspace=workspace,
        owner_id=owner_id,
        title=note_id,
        folder=folder,
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )


def add_notes(database, *notes) -> None:
    """Insert Note rows directly into ``database``, bypassing the write pipeline."""
    from sqlmodel import Session

    with Session(database.engine) as session:
        for note in notes:
            session.add(note)
        session.commit()


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


def flatten_routes(routes: list) -> list:
    """Every route object reachable from ``routes``, unwrapping FastAPI 0.141's nested
    ``_IncludedRouter`` wrapper (it keeps an included router's routes as a wrapper object
    instead of flattening them into the parent app's route list)."""
    out: list = []
    for route in routes:
        out.append(route)
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            out.extend(flatten_routes(original_router.routes))
    return out


def make_logging_app(resources=None):
    """A FastAPI app wrapped in LoggingMiddleware, with logging set up.

    Register routes on the result. Imported late so the env block in conftest.py runs
    before kajet_turbo — see tests/CLAUDE.md.
    """
    from fastapi import FastAPI

    from kajet_turbo.log import LoggingMiddleware, setup_logging

    setup_logging()
    app = FastAPI()
    app.add_middleware(LoggingMiddleware, resources=resources)
    return app


def related_item(note_id: str = "n2"):
    """One fully-populated service-layer item; its wire form is ``RELATED_ITEM_JSON``."""
    from kajet_turbo.repositories.notes import RelatedNoteItem

    return RelatedNoteItem(
        note_id=note_id,
        title="Soup",
        folder="Recipes",
        updated_at="2026-01-02T00:00:00+00:00",
        source_chunk_id="c-src",
        target_chunk_id="c-tgt",
        source_header_path=["Dinner"],
        target_header_path=["Recipes", "Soup"],
        target_content="tomato soup",
        best_distance=0.25,
        hub_margin=0.1,
        coverage=0.5,
        score=0.8,
    )


RELATED_ITEM_JSON = {
    "note_id": "n2",
    "title": "Soup",
    "folder": "Recipes",
    "updated_at": "2026-01-02T00:00:00+00:00",
    "source_chunk_id": "c-src",
    "target_chunk_id": "c-tgt",
    "source_header_path": ["Dinner"],
    "target_header_path": ["Recipes", "Soup"],
    "target_content": "tomato soup",
    "best_distance": 0.25,
    "hub_margin": 0.1,
    "coverage": 0.5,
    "score": 0.8,
}


class FakeRelatedService:
    """Stands in for ``NoteRelatedService`` behind REST and MCP alike — both surfaces only
    call ``related_async``. Records what the caller forwards; ``result`` may also be an
    exception to raise."""

    def __init__(self, result: RelatedNotesResult | Exception | None):
        self.result = result
        self.calls: list[tuple] = []

    async def related_async(self, note_id, owner_id, workspace, *, folder=None, limit=5):
        self.calls.append((note_id, owner_id, workspace, folder, limit))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result
