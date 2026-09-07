from pathlib import Path

from kajet_turbo.services.targets import WorkspaceTarget


def _ws(ws_path) -> WorkspaceTarget:
    return WorkspaceTarget(owner_id="u1", name="test-ws", path=Path(ws_path))


def test_valid_token_returns_note_html(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.save(_ws(workspace), "Shared Note", "# Hello\n\nBody text.", [])[
        "note_id"
    ]
    link = auth_client.share_link_repo.create(note_id, "test-ws", "u1")

    response = client.get(f"/api/public/notes/{link.token}")

    assert response.status_code == 200
    data = response.json()
    assert data["note_id"] == note_id
    assert data["title"] == "Shared Note"
    assert "<h1>Hello</h1>" in data["content_html"]
    assert "Body text." in data["content_html"]
    assert response.headers["cache-control"] == "no-store"


def test_revoked_token_returns_404_not_403(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.save(_ws(workspace), "Revoked Note", "content", [])["note_id"]
    link = auth_client.share_link_repo.create(note_id, "test-ws", "u1")
    assert auth_client.share_link_repo.revoke("u1", link.token) is True

    response = client.get(f"/api/public/notes/{link.token}")

    assert response.status_code == 404
    # A revoked token must 404 on the very next request even through a caching proxy --
    # this specifically caught a bug where HTTPException's headers were silently dropped
    # by the global exception handler, so only the 200 path ever carried no-store.
    assert response.headers["cache-control"] == "no-store"


def test_wikilink_to_a_private_note_never_leaks_a_link(auth_client):
    # #348: the public render must omit wl_resolver/xws_resolver entirely, so a
    # [[wikilink]] degrades to plain text -- even though the target note is real and
    # would resolve to a live <a href> on any authenticated render path, an anonymous
    # viewer of the shared note must not learn its folder/note_id or reach it.
    client, note_service, workspace = auth_client
    note_service.save(_ws(workspace), "Private Note", "secret content", [])
    note_id = note_service.save(
        _ws(workspace), "Shared Note", "See [[Private Note]] for details.", []
    )["note_id"]
    link = auth_client.share_link_repo.create(note_id, "test-ws", "u1")

    response = client.get(f"/api/public/notes/{link.token}")

    assert response.status_code == 200
    html = response.json()["content_html"]
    assert '<span class="wikilink-broken">Private Note</span>' in html
    assert "<a" not in html


def test_unknown_token_returns_404_with_no_identity_at_all(anon_client):
    # anon_client seeds no user, no note, and no share link -- this is the case that
    # proves the endpoint needs zero session/OAuth state, not merely "a bad token 404s".
    response = anon_client.client.get("/api/public/notes/does-not-exist")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
