import pytest
from sqlmodel import Session
from starlette.testclient import TestClient

from kajet_turbo.api.workspace_remote import router
from kajet_turbo.dependencies import (
    CurrentUser,
    get_required_user,
    get_target_resolver,
    get_workspace_remote_service,
)
from kajet_turbo.models import SshKey, User
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.ssh_keys import SshKeyRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
from kajet_turbo.services.targets import TargetResolver
from kajet_turbo.services.workspace_remote import WorkspaceRemoteService
from tests.api.conftest import build_test_app

_REMOTE_FIELDS = {"origin_url", "ssh_key_id", "enabled", "dirty_at", "pushed_at", "last_error"}


class _FakeWorkspaceService:
    """Just enough of WorkspaceService for TargetResolver.workspace() -- runs the real
    resolve_workspace_target dependency (and its 403 wiring) instead of stubbing it out."""

    def __init__(self, tmp_path, access: bool) -> None:
        self._tmp_path = tmp_path
        self._access = access

    def has_access(self, _user_id: str, _name: str) -> bool:
        return self._access

    def workspace_path(self, _user_id: str, name: str) -> str:
        return str(self._tmp_path / name)


def _app(database, monkeypatch, tmp_path, *, user_id="u1", access=True):
    if user_id:
        with Session(database.engine) as s:
            s.add(User(id=user_id, email="u@e.com", created_at="2026-01-01"))
            s.flush()
            s.add(
                SshKey(
                    id="k1",
                    user_id=user_id,
                    name="laptop",
                    algorithm="ed25519",
                    public_key="p",
                    private_key_enc=b"e",
                    fingerprint="f",
                    created_at="2026-01-01",
                )
            )
            s.commit()
    svc = WorkspaceRemoteService(
        WorkspaceRemoteRepository(database.engine),
        SshKeyRepository(database.engine),
        JobRepository(database.engine),
        workspaces_dir=str(tmp_path),
    )
    app = build_test_app(routers=(router,))
    app.dependency_overrides[get_workspace_remote_service] = lambda: svc
    app.dependency_overrides[get_target_resolver] = lambda: TargetResolver(
        NoteRepository(database.engine),
        _FakeWorkspaceService(tmp_path, access),  # ty: ignore[invalid-argument-type] - duck-typed stub, only has_access/workspace_path are used
    )
    if user_id:
        app.dependency_overrides[get_required_user] = lambda: CurrentUser(
            id=user_id, email="", timezone="", locale=""
        )
    return TestClient(app)


def test_get_set_delete_flow(database, monkeypatch, tmp_path):
    client = _app(database, monkeypatch, tmp_path)
    assert client.get("/api/workspaces/ws/remote").json() == {"remote": None}

    put = client.put(
        "/api/workspaces/ws/remote",
        json={"origin_url": "git@h:/r.git", "ssh_key_id": "k1", "enabled": True},
    )
    assert put.status_code == 200
    remote = put.json()["remote"]
    assert set(remote) == _REMOTE_FIELDS
    assert remote["origin_url"] == "git@h:/r.git"

    got = client.get("/api/workspaces/ws/remote").json()["remote"]
    assert got["ssh_key_id"] == "k1" and got["enabled"] is True

    assert client.delete("/api/workspaces/ws/remote").json() == {"ok": True}
    assert client.get("/api/workspaces/ws/remote").json() == {"remote": None}

    missing = client.delete("/api/workspaces/ws/remote")
    assert missing.status_code == 404
    assert missing.json() == {"error": "WORKSPACE_REMOTE_NOT_FOUND"}


def test_put_unknown_key_400(database, monkeypatch, tmp_path):
    client = _app(database, monkeypatch, tmp_path)
    r = client.put(
        "/api/workspaces/ws/remote",
        json={"origin_url": "git@h:r.git", "ssh_key_id": "nope", "enabled": True},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "WORKSPACE_REMOTE_INVALID_INPUT"
    assert "detail" in body


def test_put_https_origin_400(database, monkeypatch, tmp_path):
    client = _app(database, monkeypatch, tmp_path)
    r = client.put(
        "/api/workspaces/ws/remote",
        json={"origin_url": "https://github.com/u/r.git", "ssh_key_id": "k1", "enabled": True},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "WORKSPACE_REMOTE_INVALID_INPUT"


@pytest.mark.parametrize(
    "body",
    [
        {"ssh_key_id": "k1", "enabled": True},  # missing origin_url
        {"origin_url": "", "ssh_key_id": "k1", "enabled": True},  # blank origin_url
        {"origin_url": "git@h:r.git", "ssh_key_id": "", "enabled": True},  # blank ssh_key_id
        {"origin_url": "git@h:r.git"},  # missing ssh_key_id
        [],  # not an object at all
    ],
)
def test_put_invalid_body_422(database, monkeypatch, tmp_path, body):
    client = _app(database, monkeypatch, tmp_path)
    r = client.put("/api/workspaces/ws/remote", json=body)
    assert r.status_code == 422
    assert r.json()["error"] == "INVALID_INPUT"


def test_manual_push_enqueues(database, monkeypatch, tmp_path):
    client = _app(database, monkeypatch, tmp_path)
    client.put(
        "/api/workspaces/ws/remote",
        json={"origin_url": "git@h:r.git", "ssh_key_id": "k1", "enabled": True},
    )
    assert client.post("/api/workspaces/ws/remote/push").json() == {"ok": True}
    jobs = JobRepository(database.engine).list_jobs("u1")
    assert jobs[0].kind == "push_workspace"
    # trigger_push marks the remote dirty -- confirm the response model round-trips it
    # instead of only being sanity-checked through the pre-migration JSONResponse.
    got = client.get("/api/workspaces/ws/remote").json()["remote"]
    assert got["dirty_at"] is not None


def test_manual_push_no_remote_400(database, monkeypatch, tmp_path):
    client = _app(database, monkeypatch, tmp_path)
    r = client.post("/api/workspaces/ws/remote/push")
    assert r.status_code == 400
    assert r.json() == {"error": "WORKSPACE_REMOTE_NOT_CONFIGURED"}


@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("get", "/api/workspaces/ws/remote", {}),
        (
            "put",
            "/api/workspaces/ws/remote",
            {"json": {"origin_url": "git@h:r.git", "ssh_key_id": "k1", "enabled": True}},
        ),
        ("delete", "/api/workspaces/ws/remote", {}),
        ("post", "/api/workspaces/ws/remote/push", {}),
    ],
)
def test_forbidden_without_access(database, monkeypatch, tmp_path, method, path, kwargs):
    client = _app(database, monkeypatch, tmp_path, access=False)
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 403
    assert r.json() == {"error": "ACCESS_DENIED"}


@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("get", "/api/workspaces/ws/remote", {}),
        ("put", "/api/workspaces/ws/remote", {"json": []}),  # bad body still 401s first
        ("delete", "/api/workspaces/ws/remote", {}),
        ("post", "/api/workspaces/ws/remote/push", {}),
    ],
)
def test_requires_login(database, monkeypatch, tmp_path, method, path, kwargs):
    client = _app(database, monkeypatch, tmp_path, user_id=None)
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 401
