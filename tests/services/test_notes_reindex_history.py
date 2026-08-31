"""reindex/history/versions/restore coverage for NoteService."""

import pytest


def test_reindex_rebuilds_fts(service, workspace):
    from kajet_turbo.workspace import NoteFrontmatter, note_filepath, write_note_file

    path = note_filepath(str(workspace), "", "Zewnętrzna notatka")
    write_note_file(
        path,
        NoteFrontmatter(
            id="ext001",
            title="Zewnętrzna notatka",
            tags=[],
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        ),
        "treść zewnętrzna",
    )
    result = service.reindex("ws", owner_id="u1", ws_path=str(workspace))
    assert result["count"] == 1
    found = service._chunk_repo.search_fts("Zewnętrzna", "ws", owner_id="u1")
    assert any(n["note_id"] == "ext001" for n in found)


def test_reindex_finds_notes_in_subfolders(service, workspace, note_file_factory):
    note_file_factory(workspace, "Root note", note_id="root-id")
    note_file_factory(workspace, "Nested note", note_id="nested-id", folder="docs")

    result = service.reindex("ws", owner_id="u1", ws_path=str(workspace))

    assert result["count"] == 2


def test_reindex_batches_note_writes_and_tag_sync(
    service, workspace, note_file_factory, monkeypatch
):
    """Both new notes land in ONE DB transaction (reconcile_paths wraps every insert/
    update for the whole reconcile in a single ``operation()``, not one commit per
    note), and tag sync stays a single batched call."""
    from contextlib import contextmanager

    note_file_factory(workspace, "First", note_id="first", tags=["one"])
    note_file_factory(workspace, "Second", note_id="second", tags=["two"])
    note_op = service._crud_repo.operation
    sync_many = service._tag_repo.sync_note_tags_many
    calls = {"note_op": 0, "tags": 0}

    @contextmanager
    def record_note_op(*args, **kwargs):
        calls["note_op"] += 1
        with note_op(*args, **kwargs) as op:
            yield op

    def record_tags(workspace_name, owner_id, tagged_by_note):
        calls["tags"] += 1
        return sync_many(workspace_name, owner_id, tagged_by_note)

    monkeypatch.setattr(service._crud_repo, "operation", record_note_op)
    monkeypatch.setattr(service._tag_repo, "sync_note_tags_many", record_tags)

    result = service.reindex("ws", owner_id="u1", ws_path=str(workspace))

    assert result["count"] == 2
    assert calls == {"note_op": 1, "tags": 1}


def test_get_history_returns_commits(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Historia", "v1", [])
    note_id = result["note_id"]
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha,
        content="v2",
    )

    history = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))

    assert len(history) == 2
    assert all("sha" in h and "message" in h and "timestamp" in h for h in history)


def test_get_history_raises_for_unknown_note(service, workspace):
    with pytest.raises(ValueError):
        service.get_history("nie-ma", owner_id="u1", ws_path=str(workspace))


def test_get_version_returns_historical_content(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Historia", "treść oryginalna", [])
    note_id = result["note_id"]
    sha_v1 = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha_v1, content="treść nowa"
    )

    version = service.get_version(note_id, sha_v1, owner_id="u1", ws_path=str(workspace))

    assert version["content"] == "treść oryginalna"
    assert version["note_id"] == note_id


def test_get_version_falls_back_to_db_title_for_explicit_null_frontmatter(service, workspace):
    """get_version's or_default falls back to the DB row when a frontmatter field is
    missing OR explicitly null — pinned deliberately after #104's parser consolidation
    changed this from str(None) (the literal string) to the DB value."""
    from pathlib import Path

    from kajet_turbo.repositories.git import GitRepository
    from kajet_turbo.workspace import note_filepath

    result = service.save("u1", "ws", str(workspace), "Historia", "treść", [])
    note_id = result["note_id"]
    path = note_filepath(str(workspace), "", "Historia")
    Path(path).write_text(
        f"---\nid: {note_id}\ntitle:\ntags: []\n---\ntreść zmieniona\n", encoding="utf-8"
    )
    relative = str(Path(path).relative_to(workspace))
    GitRepository(str(workspace)).commit_file(relative, "note: hand-edit null title")
    sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    version = service.get_version(note_id, sha, owner_id="u1", ws_path=str(workspace))

    assert version["title"] == "Historia"  # DB fallback, not the literal string "None"


def test_restore_version_reverts_content(service, workspace):
    result = service.save("u1", "ws", str(workspace), "Historia", "treść oryginalna", [])
    note_id = result["note_id"]
    sha_v1 = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha_v1, content="treść nowa"
    )

    service.restore_version(note_id, sha_v1, owner_id="u1", ws_path=str(workspace))

    current = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert current.content == "treść oryginalna"


def test_restore_version_reverts_tags_and_extras(service, workspace):
    """#105: restoring a version brings back its tags AND custom frontmatter keys, not
    the current state — while id/created_at/title/folder stay put and updated_at bumps."""
    from dataclasses import replace
    from pathlib import Path

    from kajet_turbo.repositories.git import GitRepository
    from kajet_turbo.workspace import note_filepath, read_note_file, write_note_file

    result = service.save("u1", "ws", str(workspace), "Historia", "treść v1", ["stary"])
    note_id = result["note_id"]
    original_note = service._crud_repo.get(note_id, owner_id="u1")

    path = note_filepath(str(workspace), "", "Historia")
    meta, content = read_note_file(path)
    write_note_file(path, replace(meta, extras={"aliases": ["V1"]}), content)
    relative = str(Path(path).relative_to(workspace))
    GitRepository(str(workspace)).commit_file(relative, "note: hand-edit extras for v1")
    sha_v1 = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]

    service.update(
        note_id,
        owner_id="u1",
        ws_path=str(workspace),
        expected_sha=sha_v1,
        content="treść v2",
        tags=["nowy"],
    )

    service.restore_version(note_id, sha_v1, owner_id="u1", ws_path=str(workspace))

    current = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert current.content == "treść v1"
    assert current.tags == ["stary"]
    restored_meta, _ = read_note_file(path)
    assert restored_meta.extras == {"aliases": ["V1"]}

    restored_note = service._crud_repo.get(note_id, owner_id="u1")
    assert restored_note.id == original_note.id
    assert restored_note.created_at == original_note.created_at
    assert restored_note.title == original_note.title
    assert restored_note.folder == original_note.folder
    assert restored_note.updated_at != original_note.updated_at


def test_restore_version_still_works_after_expected_sha_added(service, workspace):
    note_id = service.save("u1", "ws", str(workspace), "Historia", "oryginalna", [])["note_id"]
    sha_v1 = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha_v1, content="nowa"
    )

    service.restore_version(note_id, sha_v1, owner_id="u1", ws_path=str(workspace))

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "oryginalna"


def test_nested_restore_releases_workspace_before_reindexing(service, workspace, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    note_id = service.save("u1", "ws", str(workspace), "Historia", "oryginalna", [])["note_id"]
    sha_v1 = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    service.update(
        note_id, owner_id="u1", ws_path=str(workspace), expected_sha=sha_v1, content="nowa"
    )
    current_sha = service.get_history(note_id, owner_id="u1", ws_path=str(workspace))[0]["sha"]
    index_started = Event()
    release_index = Event()

    def paused_index(*_args, **_kwargs) -> None:
        index_started.set()
        assert release_index.wait(timeout=5)

    monkeypatch.setattr(service._indexer, "index_note", paused_index)
    with ThreadPoolExecutor(max_workers=2) as pool:
        restore = pool.submit(
            service.restore_version,
            note_id,
            sha_v1,
            owner_id="u1",
            ws_path=str(workspace),
            expected_sha=current_sha,
        )
        assert index_started.wait(timeout=5)
        try:
            add = pool.submit(service.add_tags, note_id, "u1", str(workspace), ["extra"])
            add.result(timeout=2)
        finally:
            release_index.set()
        restore.result(timeout=5)

    note = service.get_with_content(note_id, owner_id="u1", ws_path=str(workspace))
    assert note.content == "oryginalna"
    assert note.tags == ["extra"]
