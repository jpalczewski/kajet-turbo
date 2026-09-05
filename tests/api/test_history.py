from pathlib import Path

from kajet_turbo.markdown import EditSpec
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget


def _ws(ws_path) -> WorkspaceTarget:
    return WorkspaceTarget(owner_id="u1", name="test-ws", path=Path(ws_path))


def _note(ws_path, note_id) -> NoteTarget:
    return NoteTarget(note_id=note_id, workspace=_ws(ws_path))


def test_note_history_returns_commits(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.save(_ws(workspace), "History", "v1", [])["note_id"]
    sha = note_service.get_history(_note(workspace, note_id))[0]["sha"]
    note_service.update(_note(workspace, note_id), expected_sha=sha, edit=EditSpec(content="v2"))

    response = client.get(f"/api/workspaces/test-ws/notes/{note_id}/history")

    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 2
    assert {"sha", "message", "timestamp"} <= entries[0].keys()


def test_note_history_requires_login(anon_client):
    response = anon_client.get("/api/workspaces/test-ws/notes/note-id/history")

    assert response.status_code == 401


def test_note_history_requires_access(no_access_client):
    response = no_access_client.get("/api/workspaces/test-ws/notes/note-id/history")

    assert response.status_code == 403


def test_note_version_returns_historical_content(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.save(_ws(workspace), "Version", "old content", [])["note_id"]
    version = note_service.get_history(_note(workspace, note_id))[0]["sha"]
    note_service.update(
        _note(workspace, note_id),
        expected_sha=version,
        edit=EditSpec(content="new content"),
    )

    response = client.get(f"/api/workspaces/test-ws/notes/{note_id}/history/{version}")

    assert response.status_code == 200
    assert "old content" in response.json()["content_html"]


def test_restore_note_version_reverts_content(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.save(_ws(workspace), "Restore", "original", [])["note_id"]
    version = note_service.get_history(_note(workspace, note_id))[0]["sha"]
    note_service.update(
        _note(workspace, note_id),
        expected_sha=version,
        edit=EditSpec(content="new content"),
    )

    response = client.post(f"/api/workspaces/test-ws/notes/{note_id}/history/{version}/restore")

    current = note_service.get_with_content(_note(workspace, note_id))
    assert response.status_code == 200
    assert current.content == "original"


def test_restore_note_version_response_matches_declared_schema(auth_client):
    # note_service.restore_version delegates to note_service.update, whose return dict
    # carries internal fields (e.g. "replaced", added for replace_all support) that this
    # REST endpoint doesn't expose — the response must stay pinned to
    # RestoreVersionResponse's documented shape, not leak them.
    client, note_service, workspace = auth_client
    note_id = note_service.save(_ws(workspace), "Restore", "original", [])["note_id"]
    version = note_service.get_history(_note(workspace, note_id))[0]["sha"]
    note_service.update(
        _note(workspace, note_id),
        expected_sha=version,
        edit=EditSpec(content="new content"),
    )

    response = client.post(f"/api/workspaces/test-ws/notes/{note_id}/history/{version}/restore")

    assert response.json() == {"note_id": note_id, "warnings": []}
