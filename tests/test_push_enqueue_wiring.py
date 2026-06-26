from sqlmodel import Session

from kajet_turbo.models import SshKey, User
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
from kajet_turbo.services.push_enqueue import make_enqueue_push_on_commit


def _seed(engine):
    with Session(engine) as s:
        s.add(User(id="u1", email="u@e.com", created_at="2026-01-01"))
        s.flush()
        s.add(
            SshKey(
                id="k1",
                user_id="u1",
                name="laptop",
                algorithm="ed25519",
                public_key="p",
                private_key_enc=b"e",
                fingerprint="f",
                created_at="2026-01-01",
            )
        )
        s.commit()


def test_enqueue_hook_enqueues_for_enabled_remote(database, tmp_path):
    _seed(database.engine)
    remotes = WorkspaceRemoteRepository(database.engine)
    jobs = JobRepository(database.engine)
    remotes.upsert("u1", "ws", origin_url="o", ssh_key_id="k1", enabled=True, now="t")
    workspaces_dir = tmp_path
    ws_path = workspaces_dir / "u1" / "ws"
    ws_path.mkdir(parents=True)

    hook = make_enqueue_push_on_commit(jobs, remotes, str(workspaces_dir))
    hook(str(ws_path))

    listed = jobs.list_jobs("u1")
    assert [j.kind for j in listed] == ["push_workspace"]
    assert remotes.get("u1", "ws").dirty_at is not None


def test_enqueue_hook_noop_without_remote(database, tmp_path):
    _seed(database.engine)
    remotes = WorkspaceRemoteRepository(database.engine)
    jobs = JobRepository(database.engine)
    workspaces_dir = tmp_path
    ws_path = workspaces_dir / "u1" / "ws"
    ws_path.mkdir(parents=True)

    hook = make_enqueue_push_on_commit(jobs, remotes, str(workspaces_dir))
    hook(str(ws_path))

    assert jobs.list_jobs("u1") == []


def test_enqueue_hook_noop_when_disabled(database, tmp_path):
    _seed(database.engine)
    remotes = WorkspaceRemoteRepository(database.engine)
    jobs = JobRepository(database.engine)
    remotes.upsert("u1", "ws", origin_url="o", ssh_key_id="k1", enabled=False, now="t")
    workspaces_dir = tmp_path
    ws_path = workspaces_dir / "u1" / "ws"
    ws_path.mkdir(parents=True)

    make_enqueue_push_on_commit(jobs, remotes, str(workspaces_dir))(str(ws_path))
    assert jobs.list_jobs("u1") == []
