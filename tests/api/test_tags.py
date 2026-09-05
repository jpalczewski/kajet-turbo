from pathlib import Path

from kajet_turbo.services.targets import WorkspaceTarget


def _ws(ws_path) -> WorkspaceTarget:
    return WorkspaceTarget(owner_id="u1", name="test-ws", path=Path(ws_path))


def test_list_tags_endpoint(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save(_ws(ws_path), "A", "body", ["work/projects"])
    resp = client.get("/api/workspaces/test-ws/tags")
    assert resp.status_code == 200
    paths = {t["path"] for t in resp.json()["tags"]}
    assert paths == {"work", "work/projects"}


def test_list_tags_403_when_no_access(no_access_client):
    resp = no_access_client.get("/api/workspaces/test-ws/tags")
    assert resp.status_code == 403


def test_list_notes_by_tag_prefix(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save(_ws(ws_path), "A", "b", ["work/projects"])
    resp = client.get("/api/workspaces/test-ws/notes?tag=work")
    assert resp.status_code == 200
    assert {n["title"] for n in resp.json()["notes"]} == {"A"}
    exact = client.get("/api/workspaces/test-ws/notes?tag=work&include_descendants=false")
    assert exact.json()["notes"] == []
