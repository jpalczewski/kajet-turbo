from pathlib import Path

from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget


def _ws(ws_path) -> WorkspaceTarget:
    return WorkspaceTarget(owner_id="u1", name="test-ws", path=Path(ws_path))


def _note(ws_path, note_id) -> NoteTarget:
    return NoteTarget(note_id=note_id, workspace=_ws(ws_path))


def test_create_share_link_returns_token(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.create.save(_ws(workspace), "Shared", "content", [])["note_id"]

    response = client.post(f"/api/workspaces/test-ws/notes/{note_id}/share-links", json={})

    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {
        "token",
        "created_at",
        "visit_count",
        "last_visited_at",
        "page_view_count",
        "last_page_viewed_at",
        "preview_description",
    }
    assert body["token"]
    assert body["visit_count"] == 0
    assert body["last_visited_at"] is None
    assert body["preview_description"] is False


def test_create_share_link_with_preview_description(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.create.save(_ws(workspace), "Shared", "content", [])["note_id"]

    response = client.post(
        f"/api/workspaces/test-ws/notes/{note_id}/share-links",
        json={"preview_description": True},
    )

    assert response.status_code == 201
    assert response.json()["preview_description"] is True


def test_list_share_links_omits_revoked(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.create.save(_ws(workspace), "Shared", "content", [])["note_id"]
    kept = client.post(f"/api/workspaces/test-ws/notes/{note_id}/share-links", json={}).json()
    revoked = client.post(f"/api/workspaces/test-ws/notes/{note_id}/share-links", json={}).json()
    client.delete(f"/api/workspaces/test-ws/notes/{note_id}/share-links/{revoked['token']}")

    response = client.get(f"/api/workspaces/test-ws/notes/{note_id}/share-links")

    assert response.status_code == 200
    tokens = [link["token"] for link in response.json()["links"]]
    assert tokens == [kept["token"]]


def test_list_share_links_includes_visit_summary(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.create.save(_ws(workspace), "Shared", "content", [])["note_id"]
    link = client.post(f"/api/workspaces/test-ws/notes/{note_id}/share-links", json={}).json()
    client.get(f"/api/public/notes/{link['token']}")

    response = client.get(f"/api/workspaces/test-ws/notes/{note_id}/share-links")

    assert response.status_code == 200
    assert response.json()["links"] == [
        {
            "token": link["token"],
            "created_at": link["created_at"],
            "visit_count": 1,
            "page_view_count": 0,
            "last_page_viewed_at": None,
            "preview_description": False,
            "last_visited_at": response.json()["links"][0]["last_visited_at"],
        }
    ]
    assert response.json()["links"][0]["last_visited_at"] is not None


def test_revoke_share_link_404s_the_public_endpoint(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.create.save(_ws(workspace), "Shared", "content", [])["note_id"]
    token = client.post(f"/api/workspaces/test-ws/notes/{note_id}/share-links", json={}).json()[
        "token"
    ]

    revoke_response = client.delete(f"/api/workspaces/test-ws/notes/{note_id}/share-links/{token}")
    public_response = client.get(f"/api/public/notes/{token}")

    assert revoke_response.status_code == 200
    assert revoke_response.json() == {"ok": True}
    assert public_response.status_code == 404


def test_revoke_unknown_token_404s(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.create.save(_ws(workspace), "Shared", "content", [])["note_id"]

    response = client.delete(f"/api/workspaces/test-ws/notes/{note_id}/share-links/does-not-exist")

    assert response.status_code == 404


def test_revoke_rejects_token_from_a_different_note(auth_client):
    # A note-scoped revoke URL must not be able to revoke a token that belongs to a
    # different note the same owner controls -- the note_id in the path is not decorative.
    client, note_service, workspace = auth_client
    note_a = note_service.create.save(_ws(workspace), "Note A", "a", [])["note_id"]
    note_b = note_service.create.save(_ws(workspace), "Note B", "b", [])["note_id"]
    token = client.post(f"/api/workspaces/test-ws/notes/{note_b}/share-links", json={}).json()[
        "token"
    ]

    response = client.delete(f"/api/workspaces/test-ws/notes/{note_a}/share-links/{token}")
    still_public = client.get(f"/api/public/notes/{token}")

    assert response.status_code == 404
    assert still_public.status_code == 200


def test_update_share_link_preview_toggles_flag(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.create.save(_ws(workspace), "Shared", "content", [])["note_id"]
    token = client.post(f"/api/workspaces/test-ws/notes/{note_id}/share-links", json={}).json()[
        "token"
    ]

    response = client.patch(
        f"/api/workspaces/test-ws/notes/{note_id}/share-links/{token}",
        json={"preview_description": True},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    links = client.get(f"/api/workspaces/test-ws/notes/{note_id}/share-links").json()["links"]
    assert next(link for link in links if link["token"] == token)["preview_description"] is True


def test_update_share_link_preview_unknown_token_404s(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.create.save(_ws(workspace), "Shared", "content", [])["note_id"]

    response = client.patch(
        f"/api/workspaces/test-ws/notes/{note_id}/share-links/does-not-exist",
        json={"preview_description": True},
    )

    assert response.status_code == 404


def test_update_share_link_preview_rejects_token_from_a_different_note(auth_client):
    client, note_service, workspace = auth_client
    note_a = note_service.create.save(_ws(workspace), "Note A", "a", [])["note_id"]
    note_b = note_service.create.save(_ws(workspace), "Note B", "b", [])["note_id"]
    token = client.post(f"/api/workspaces/test-ws/notes/{note_b}/share-links", json={}).json()[
        "token"
    ]

    response = client.patch(
        f"/api/workspaces/test-ws/notes/{note_a}/share-links/{token}",
        json={"preview_description": True},
    )

    assert response.status_code == 404


def test_share_links_require_login(anon_client):
    assert anon_client.post("/api/workspaces/test-ws/notes/note-id/share-links").status_code == 401
    assert anon_client.get("/api/workspaces/test-ws/notes/note-id/share-links").status_code == 401
    assert (
        anon_client.patch(
            "/api/workspaces/test-ws/notes/note-id/share-links/tok",
            json={"preview_description": True},
        ).status_code
        == 401
    )
    assert (
        anon_client.delete("/api/workspaces/test-ws/notes/note-id/share-links/tok").status_code
        == 401
    )


def test_share_links_require_workspace_access(no_access_client):
    response = no_access_client.post("/api/workspaces/test-ws/notes/note-id/share-links")

    assert response.status_code == 403
