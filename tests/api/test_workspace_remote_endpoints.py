from fastapi import FastAPI
from sqlmodel import Session
from starlette.testclient import TestClient

from kajet_turbo.api.workspace_remote import router
from kajet_turbo.dependencies import get_workspace_remote_service, get_workspace_service
from kajet_turbo.models import SshKey, User
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.ssh_keys import SshKeyRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
from kajet_turbo.services.workspace_remote import WorkspaceRemoteService


class _AccessStub:
    def has_access(self, _user_id, _workspace):
        return True


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
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_workspace_remote_service] = lambda: svc

    class _Access:
        def has_access(self, _u, _w):
            return access

    app.dependency_overrides[get_workspace_service] = _Access
    monkeypatch.setattr(
        "kajet_turbo.api.workspace_remote.get_session_user",
        lambda _r: {"id": user_id} if user_id else None,
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
    assert put.json()["remote"]["origin_url"] == "git@h:/r.git"

    got = client.get("/api/workspaces/ws/remote").json()["remote"]
    assert got["ssh_key_id"] == "k1" and got["enabled"] is True

    assert client.delete("/api/workspaces/ws/remote").status_code == 200
    assert client.get("/api/workspaces/ws/remote").json() == {"remote": None}
    assert client.delete("/api/workspaces/ws/remote").status_code == 404


def test_put_unknown_key_400(database, monkeypatch, tmp_path):
    client = _app(database, monkeypatch, tmp_path)
    r = client.put(
        "/api/workspaces/ws/remote",
        json={"origin_url": "git@h:r.git", "ssh_key_id": "nope", "enabled": True},
    )
    assert r.status_code == 400


def test_put_https_origin_400(database, monkeypatch, tmp_path):
    client = _app(database, monkeypatch, tmp_path)
    r = client.put(
        "/api/workspaces/ws/remote",
        json={"origin_url": "https://github.com/u/r.git", "ssh_key_id": "k1", "enabled": True},
    )
    assert r.status_code == 400


def test_manual_push_enqueues(database, monkeypatch, tmp_path):
    client = _app(database, monkeypatch, tmp_path)
    client.put(
        "/api/workspaces/ws/remote",
        json={"origin_url": "git@h:r.git", "ssh_key_id": "k1", "enabled": True},
    )
    assert client.post("/api/workspaces/ws/remote/push").status_code == 200
    assert JobRepository(database.engine).list_jobs("u1")[0].kind == "push_workspace"


def test_manual_push_no_remote_400(database, monkeypatch, tmp_path):
    client = _app(database, monkeypatch, tmp_path)
    assert client.post("/api/workspaces/ws/remote/push").status_code == 400


def test_forbidden_without_access(database, monkeypatch, tmp_path):
    client = _app(database, monkeypatch, tmp_path, access=False)
    assert client.get("/api/workspaces/ws/remote").status_code == 403


def test_requires_login(database, monkeypatch, tmp_path):
    client = _app(database, monkeypatch, tmp_path, user_id=None)
    assert client.get("/api/workspaces/ws/remote").status_code == 401
