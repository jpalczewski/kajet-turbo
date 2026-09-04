"""save/get/outline coverage for NoteService."""

from unittest.mock import patch

import pytest

from kajet_turbo import perf
from tests.services.helpers import make_flaky_db_write


def test_save_perf_span_records_phases(service, workspace):
    with perf.perf_span() as span:
        service.save("u1", "ws", str(workspace), "Perf", "# Head\n\nbody text", [])
    # FTS-only test indexer => no embedding HTTP, but git/db/chunk phases are recorded.
    assert span.fields["git_ms"] > 0
    assert span.fields["workspace_write_ms"] >= span.fields["git_ms"]
    assert "git_lock_wait_ms" in span.fields
    assert "db_ms" in span.fields
    assert span.fields["chunks"] >= 1


def test_older_index_callback_cannot_overwrite_newer_edit(service, workspace, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from kajet_turbo.services import indexing as indexing_module

    note_id = service.save("u1", "ws", str(workspace), "Title", "initial body", [])["note_id"]
    initial_sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    older_chunking = Event()
    release_older = Event()
    real_chunk_markdown = indexing_module.chunk_markdown

    def paused_chunk_markdown(content, *args, **kwargs):
        if content == "older edit":
            older_chunking.set()
            assert release_older.wait(timeout=5)
        return real_chunk_markdown(content, *args, **kwargs)

    monkeypatch.setattr(indexing_module, "chunk_markdown", paused_chunk_markdown)
    with ThreadPoolExecutor(max_workers=2) as pool:
        older = pool.submit(
            service.update,
            note_id,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=initial_sha,
            content="older edit",
        )
        assert older_chunking.wait(timeout=5)
        newer_sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
        try:
            newer = pool.submit(
                service.update,
                note_id,
                owner_id="u1",
                ws_path=str(workspace),
                expected_sha=newer_sha,
                content="newer edit",
            )
            newer.result(timeout=5)
        finally:
            release_older.set()
        older.result(timeout=5)

    chunks = service._chunk_repo.get_chunks(note_id)
    indexed = " ".join(chunk["content"] for chunk in chunks)
    assert "newer edit" in indexed
    assert "older edit" not in indexed


def test_save_creates_file_and_db_record(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Testowa notatka", "treść", ["python"])
    assert "note_id" in result
    note_id = result["note_id"]
    assert (workspace / "Testowa notatka.md").exists()
    note = service._crud_repo.get(note_id, owner_id="u1")
    assert note is not None
    assert note.title == "Testowa notatka"
    assert note.owner_id == "u1"


def test_save_in_root_does_not_create_notes_directory(service, workspace):
    service.save("u1", "ws", str(workspace), "Root note", "content", [])

    assert (workspace / "Root note.md").exists()
    assert not (workspace / "notes").exists()


def test_save_rejects_duplicate_title_in_same_folder(service, workspace):
    service.save("u1", "ws", str(workspace), "Duplicate", "content", [], folder="docs")

    with pytest.raises(FileExistsError):
        service.save("u1", "ws", str(workspace), "Duplicate", "other", [], folder="docs")


def test_save_rejects_normalization_collision_with_existing_note(service, workspace):
    """ "A:B" and "A B" both sanitize to "A B.md" — a different title, same file."""
    from kajet_turbo.workspace import read_note_file

    first = service.save("u1", "ws", str(workspace), "A B", "first body", [])

    with pytest.raises(FileExistsError, match="A B"):
        service.save("u1", "ws", str(workspace), "A:B", "second body", [])

    _, content = read_note_file(str(workspace / "A B.md"))
    assert content.strip() == "first body"
    note = service.get_with_content(first["note_id"], owner_id="u1", ws_path=str(workspace))
    assert note is not None
    assert note.content.strip() == "first body"


def test_save_rejects_collision_with_orphan_file_on_disk(service, workspace):
    """A file with no matching DB row must still block a colliding save."""
    (workspace / "A B.md").write_text("orphan content\n")

    with pytest.raises(FileExistsError):
        service.save("u1", "ws", str(workspace), "A B", "new body", [])

    assert (workspace / "A B.md").read_text() == "orphan content\n"
    assert service._crud_repo.get_by_path("ws", "u1", "", "A B") is None


def test_save_rejects_normalization_collision_with_ghost_row(service, workspace):
    """A DB row whose file was deleted out-of-band must still block a colliding save —
    the check is DB-row-based (note_path_conflict), not Path.exists()-based."""
    first = service.save("u1", "ws", str(workspace), "A B", "first body", [])
    (workspace / "A B.md").unlink()

    with pytest.raises(FileExistsError, match="A B"):
        service.save("u1", "ws", str(workspace), "A:B", "second body", [])

    note = service._crud_repo.get(first["note_id"], owner_id="u1")
    assert note is not None
    assert note.title == "A B"


def test_save_git_error_rolls_back_file(service, workspace):
    from kajet_turbo.repositories.git import GitError

    # save() commits through staged_workspace_change, which always calls commit_changes
    # (even for a single file) so single- and multi-file writes share one rollback path.
    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.commit_changes",
            side_effect=GitError("fail"),
        ),
        pytest.raises(GitError),
    ):
        service.save("u1", "ws", str(workspace), "Git fail note", "treść", [])
    md_files = [p for p in workspace.rglob("*.md") if ".git" not in str(p)]
    assert md_files == []
    # #155: the row insert runs before the git commit inside the same transaction, so a
    # git-side failure must roll the already-flushed row back too, not just the file.
    assert service._crud_repo.list_notes("ws", "u1", limit=None) == []


def test_save_db_failure_leaves_no_file_and_no_row(service, workspace, monkeypatch):
    """#155: the row write runs before the tree write, so a DB-side failure must abort
    before anything ever touches disk or git."""

    flaky_insert = make_flaky_db_write(service._crud_repo.insert_in_session)
    monkeypatch.setattr(service._crud_repo, "insert_in_session", flaky_insert)

    with pytest.raises(RuntimeError, match="db exploded"):
        service.save("u1", "ws", str(workspace), "DB fail note", "treść", [])

    md_files = [p for p in workspace.rglob("*.md") if ".git" not in str(p)]
    assert md_files == []
    assert service._crud_repo.list_notes("ws", "u1", limit=None) == []


def test_get_with_content_returns_note_data(service, workspace):
    from kajet_turbo.services.notes.types import NoteData

    note_id = service.save("u1", "ws", str(workspace), "Title", "# Content", [])["note_id"]
    result = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert isinstance(result, NoteData)
    assert result.note_id == note_id
    assert result.title == "Title"
    assert result.content == "# Content"


def test_get_with_content_returns_none_for_wrong_owner(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Notatka", "treść", [])
    note_id = result["note_id"]
    assert service.get_with_content(note_id, owner_id="u2", ws_path=str(workspace)) is None


def test_get_with_content_returns_content(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Notatka", "moja treść", [])
    note_id = result["note_id"]
    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note is not None
    assert note.content == "moja treść"
    assert note.title == "Notatka"


def test_get_many_returns_notes_in_order_with_errors_for_missing(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "content one", [])
    r2 = service.save("u1", "ws", str(workspace), "Second", "content two", [])
    results = service.get_many(
        [r1["note_id"], "does-not-exist", r2["note_id"]], owner_id="u1", ws_path=str(workspace)
    )
    assert len(results) == 3
    assert results[0].note_id == r1["note_id"]
    assert results[0].content == "content one"
    assert results[1] == {
        "note_id": "does-not-exist",
        "error": "Notatka does-not-exist nie znaleziona.",
    }
    assert results[2].note_id == r2["note_id"]


def test_get_outline_returns_headings_without_content(service, workspace):
    result = service.save(
        "u1", "ws", str(workspace), "Doc", "# Doc\n\n## Tasks\n\n- one\n\n## Notes\n\ntext\n", []
    )
    outline = service.get_outline(result["note_id"], owner_id="u1", ws_path=str(workspace))
    assert outline["title"] == "Doc"
    assert [s["heading"] for s in outline["sections"]] == ["Doc", "Tasks", "Notes"]
    assert "content" not in outline
    assert "content" not in outline["sections"][0]


def test_get_outline_missing_note_returns_none(service, workspace):
    assert service.get_outline("missing", owner_id="u1", ws_path=str(workspace)) is None
