"""Direct coverage for ReindexNoteHandler (job kind reindex_note) — the file → chunks +
FTS step that used to run inline inside NoteIndexer.index_many. See services/indexing.py
and services/reindex_handler.py for the design."""

import json

from sqlmodel import Session

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.models import Note
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.notes import NoteChunkRepository, NoteRepository
from kajet_turbo.services.reindex_handler import ReindexNoteHandler
from kajet_turbo.workspace import NoteFrontmatter, note_filepath, write_note_file
from tests.services.conftest import seed_user

_CFG = EmbedderConfig(
    backend_id="b", type="openai", model="m", dim=3, base_url="http://x", api_key="k"
)


def _note(database, note_id="n1", *, title="T", generation=1, ws="ws", owner="u1"):
    with Session(database.engine) as session:
        session.add(
            Note(
                id=note_id,
                workspace=ws,
                owner_id=owner,
                title=title,
                index_generation=generation,
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.commit()


def _write_file(root, note_id: str, title: str, content: str) -> str:
    path = note_filepath(str(root), "", title)
    write_note_file(
        path,
        NoteFrontmatter(
            id=note_id,
            title=title,
            tags=[],
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
        content,
    )
    return path


def _handler(database, workspaces_dir, *, resolve_cfg=lambda o: None):
    return ReindexNoteHandler(
        note_repo=NoteRepository(database.engine),
        chunk_repo=NoteChunkRepository(database.engine),
        jobs=JobRepository(database.engine),
        resolve_cfg=resolve_cfg,
        workspaces_dir=str(workspaces_dir),
    )


def test_missing_note_is_a_terminal_noop(database, tmp_path):
    handler = _handler(database, tmp_path)
    handler({"note_id": "ghost", "workspace": "ws", "owner_id": "u1"})  # must not raise
    assert NoteChunkRepository(database.engine).get_chunks("ghost") == []


def test_missing_file_is_a_terminal_noop(database, tmp_path):
    _note(database, "n1")
    handler = _handler(database, tmp_path)  # nothing written under tmp_path/u1/ws
    handler({"note_id": "n1", "workspace": "ws", "owner_id": "u1"})  # must not raise
    assert NoteChunkRepository(database.engine).get_chunks("n1") == []


def test_superseded_generation_writes_nothing_and_enqueues_no_embed(
    database, git_workspace_factory
):
    seed_user(database, "u1")
    ws_root = git_workspace_factory("u1/ws")
    workspaces_dir = ws_root.parent.parent
    _note(database, "n1", generation=5)
    _write_file(ws_root, "n1", "T", "# T\n\nbody\n")

    note_repo = NoteRepository(database.engine)
    real_get = note_repo.get

    def stale_get(note_id, owner_id=None):
        # Simulate a handler that read the note before a newer edit bumped its generation:
        # the CAS check compares against the LIVE row (5), not this stale value (3).
        note = real_get(note_id, owner_id=owner_id)
        if note is not None:
            note.index_generation = 3
        return note

    note_repo.get = stale_get  # ty: ignore[invalid-assignment] - patch spy for stale-generation regression

    handler = ReindexNoteHandler(
        note_repo=note_repo,
        chunk_repo=NoteChunkRepository(database.engine),
        jobs=JobRepository(database.engine),
        resolve_cfg=lambda o: _CFG,
        workspaces_dir=str(workspaces_dir),
    )
    handler({"note_id": "n1", "workspace": "ws", "owner_id": "u1"})

    assert NoteChunkRepository(database.engine).get_chunks("n1") == []
    assert JobRepository(database.engine).list_jobs("u1", kind="embed_note") == []


def test_success_writes_chunks_and_enqueues_embed_job_in_one_commit(
    database, git_workspace_factory
):
    seed_user(database, "u1")
    ws_root = git_workspace_factory("u1/ws")
    workspaces_dir = ws_root.parent.parent
    _note(database, "n1", title="Title")
    _write_file(ws_root, "n1", "Title", "# Title\n\nsome body text\n")

    handler = _handler(database, workspaces_dir, resolve_cfg=lambda o: _CFG)
    handler({"note_id": "n1", "workspace": "ws", "owner_id": "u1"})

    assert len(NoteChunkRepository(database.engine).get_chunks("n1")) >= 1

    pending = JobRepository(database.engine).list_jobs("u1", kind="embed_note", status="pending")
    assert len(pending) == 1
    assert json.loads(pending[0].payload) == {
        "note_id": "n1",
        "workspace": "ws",
        "owner_id": "u1",
    }


def test_success_without_backend_writes_chunks_but_enqueues_no_embed_job(
    database, git_workspace_factory
):
    seed_user(database, "u1")
    ws_root = git_workspace_factory("u1/ws")
    workspaces_dir = ws_root.parent.parent
    _note(database, "n1", title="Title")
    _write_file(ws_root, "n1", "Title", "# Title\n\nbody\n")

    handler = _handler(database, workspaces_dir, resolve_cfg=lambda o: None)
    handler({"note_id": "n1", "workspace": "ws", "owner_id": "u1"})

    assert len(NoteChunkRepository(database.engine).get_chunks("n1")) >= 1
    assert JobRepository(database.engine).list_jobs("u1", kind="embed_note") == []


def test_resolve_cfg_error_still_commits_chunks_but_enqueues_no_embed_job(
    database, git_workspace_factory
):
    """A raising resolver (e.g. SECRET_KEY unset) must degrade the same way NoteIndexer's
    single-note path does: chunks/FTS still commit, the note just stays FTS-only — the
    resolve failure must not roll back the chunk write already staged in this transaction."""
    seed_user(database, "u1")
    ws_root = git_workspace_factory("u1/ws")
    workspaces_dir = ws_root.parent.parent
    _note(database, "n1", title="Title")
    _write_file(ws_root, "n1", "Title", "# Title\n\nbody\n")

    def raising_resolve_cfg(owner_id):
        raise RuntimeError("SECRET_KEY unset")

    handler = _handler(database, workspaces_dir, resolve_cfg=raising_resolve_cfg)
    handler({"note_id": "n1", "workspace": "ws", "owner_id": "u1"})  # must not raise

    assert len(NoteChunkRepository(database.engine).get_chunks("n1")) >= 1
    assert JobRepository(database.engine).list_jobs("u1", kind="embed_note") == []
