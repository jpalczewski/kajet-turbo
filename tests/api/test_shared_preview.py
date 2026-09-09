from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlmodel import select

from kajet_turbo.api import shared_preview
from kajet_turbo.models import NoteShareLinkVisit
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget

_SHELL = '<html><head><title>kajet</title></head><body><div id="app"></div></body></html>'


def test_page_and_content_statistics_are_separate(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.save(_ws(workspace), "Shared", "content", [])["note_id"]
    repo = auth_client.share_link_repo
    link = repo.create(note_id, "test-ws", "u1")
    assert (
        client.get(f"/shared/{link.token}", headers={"user-agent": "Preview/1"}).status_code == 200
    )
    assert client.head(f"/shared/{link.token}").status_code == 200
    summary = repo.list_active_with_visit_summary(note_id)[0]
    assert summary.page_view_count == 1
    assert summary.visit_count == 0
    assert summary.last_page_viewed_at is not None
    assert summary.last_visited_at is None
    assert client.get(f"/api/public/notes/{link.token}").status_code == 200
    summary = repo.list_active_with_visit_summary(note_id)[0]
    assert summary.page_view_count == summary.visit_count == 1
    assert summary.last_visited_at is not None
    with repo.timed_session() as session:
        visits = session.exec(select(NoteShareLinkVisit)).all()
    assert {visit.kind for visit in visits} == {"page", "content"}
    page = next(visit for visit in visits if visit.kind == "page")
    assert page.user_agent == "Preview/1"
    assert page.ip == "testclient"


def _ws(ws_path) -> WorkspaceTarget:
    return WorkspaceTarget(owner_id="u1", name="test-ws", path=Path(ws_path))


def _note(ws_path, note_id) -> NoteTarget:
    return NoteTarget(note_id=note_id, workspace=_ws(ws_path))


@pytest.fixture(autouse=True)
def fake_dist(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """`shared_preview._shell_parts` is a bare `lru_cache(maxsize=1)` -- module-global, no
    args -- so every test that hits `/shared/{token}` must point it at its own shell and
    clear the cache before relying on it, or it silently reuses whatever an earlier test
    in the same xdist worker computed first."""
    index = tmp_path / "index.html"
    index.write_text(_SHELL, encoding="utf-8")
    monkeypatch.setattr(shared_preview, "_DIST_INDEX", index)
    shared_preview._shell_parts.cache_clear()
    yield
    shared_preview._shell_parts.cache_clear()


def test_valid_token_renders_title_and_og_tags(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.save(_ws(workspace), "My Shared Note", "some content", [])["note_id"]
    token = client.post(f"/api/workspaces/test-ws/notes/{note_id}/share-links", json={}).json()[
        "token"
    ]

    response = client.get(f"/shared/{token}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert "<title>My Shared Note — kajet</title>" in body
    assert 'property="og:title" content="My Shared Note — kajet"' in body
    assert 'property="og:type" content="website"' in body
    assert f'property="og:url" content="http://testserver/shared/{token}"' in body
    assert 'name="twitter:card" content="summary"' in body
    # The shell's own static <title> (baked in at build time) must not survive alongside
    # the per-token one -- exactly one <title> in the whole document.
    assert body.count("<title>") == 1


def test_preview_description_off_by_default_leaks_no_note_content(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.save(
        _ws(workspace), "Shared", "a very secret sentence nobody should see", []
    )["note_id"]
    token = client.post(f"/api/workspaces/test-ws/notes/{note_id}/share-links", json={}).json()[
        "token"
    ]

    response = client.get(f"/shared/{token}")

    assert "secret sentence" not in response.text


def test_preview_description_on_includes_excerpt(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.save(
        _ws(workspace), "Shared", "a very secret sentence everyone should see", []
    )["note_id"]
    token = client.post(
        f"/api/workspaces/test-ws/notes/{note_id}/share-links",
        json={"preview_description": True},
    ).json()["token"]

    response = client.get(f"/shared/{token}")

    assert "secret sentence everyone should see" in response.text


def test_preview_description_excerpt_drops_leading_heading(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.save(
        _ws(workspace), "Shared", "# Shared\n\nthe actual body text starts here", []
    )["note_id"]
    token = client.post(
        f"/api/workspaces/test-ws/notes/{note_id}/share-links",
        json={"preview_description": True},
    ).json()["token"]

    response = client.get(f"/shared/{token}")

    body = response.text
    assert "the actual body text starts here" in body
    assert "# Shared" not in body


def test_title_with_special_characters_is_escaped(auth_client):
    client, note_service, workspace = auth_client
    title = 'Tom & Jerry <script>"quoted"</script>'
    note_id = note_service.save(_ws(workspace), title, "content", [])["note_id"]
    token = client.post(f"/api/workspaces/test-ws/notes/{note_id}/share-links", json={}).json()[
        "token"
    ]

    response = client.get(f"/shared/{token}")

    body = response.text
    assert "<script>" not in body
    assert "Tom &amp; Jerry" in body


def test_unknown_and_revoked_token_render_identical_neutral_bodies(auth_client):
    client, note_service, workspace = auth_client
    note_id = note_service.save(_ws(workspace), "Shared", "content", [])["note_id"]
    token = client.post(f"/api/workspaces/test-ws/notes/{note_id}/share-links", json={}).json()[
        "token"
    ]
    client.delete(f"/api/workspaces/test-ws/notes/{note_id}/share-links/{token}")

    revoked_response = client.get(f"/shared/{token}")
    unknown_response = client.get("/shared/this-token-was-never-issued")

    assert revoked_response.status_code == 200
    assert unknown_response.status_code == 200
    assert revoked_response.headers["cache-control"] == "no-store"
    assert unknown_response.headers["cache-control"] == "no-store"
    # Byte-identical modulo the url each was requested at -- the one property a validity
    # oracle would need to distinguish, and it's the only difference allowed to exist.
    revoked_body = revoked_response.text.replace(token, "TOKEN")
    unknown_body = unknown_response.text.replace("this-token-was-never-issued", "TOKEN")
    assert revoked_body == unknown_body
    with auth_client.share_link_repo.timed_session() as session:
        assert session.exec(select(NoteShareLinkVisit)).all() == []


def test_no_build_present_404s_instead_of_crashing(auth_client, tmp_path, monkeypatch):
    # An API-role deployment run without a frontend build (no `dist/index.html`) must not
    # crash the route -- it's a deployment-wide config gap, not a token-related signal.
    client, note_service, workspace = auth_client
    note_id = note_service.save(_ws(workspace), "Shared", "content", [])["note_id"]
    token = client.post(f"/api/workspaces/test-ws/notes/{note_id}/share-links", json={}).json()[
        "token"
    ]
    monkeypatch.setattr(shared_preview, "_DIST_INDEX", tmp_path / "missing" / "index.html")
    shared_preview._shell_parts.cache_clear()

    response = client.get(f"/shared/{token}")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_unknown_token_renders_neutral_title(auth_client):
    client = auth_client.client

    response = client.get("/shared/this-token-was-never-issued")

    assert response.status_code == 200
    assert "<title>Link nieaktywny — kajet</title>" in response.text


def test_head_request_is_served(auth_client):
    # A HEAD-only crawler must not 405 into falling through to the SPA mount instead --
    # @router.get alone doesn't get HEAD for free the way a plain Starlette Route does.
    client, note_service, workspace = auth_client
    note_id = note_service.save(_ws(workspace), "Shared", "content", [])["note_id"]
    token = client.post(f"/api/workspaces/test-ws/notes/{note_id}/share-links", json={}).json()[
        "token"
    ]

    response = client.head(f"/shared/{token}", headers={"accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
