from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo
from sqlmodel import Session

from kajet_turbo.crypto import cipher_for, generate_keypair
from kajet_turbo.models import SshKey, User
from kajet_turbo.repositories.git import GitError
from kajet_turbo.repositories.ssh_keys import SshKeyRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
from kajet_turbo.services.push_handler import PushHandler


def _cipher():
    return cipher_for("ssh-key", secret="server-secret")


def _handler(database, tmp_path) -> PushHandler:
    return PushHandler(
        WorkspaceRemoteRepository(database.engine),
        SshKeyRepository(database.engine),
        cipher_factory=_cipher,
        known_hosts_path=str(tmp_path / "known_hosts"),
        key_dir=str(tmp_path),
    )


def _seed_key(database, *, user_id="u1", key_id="k1") -> None:
    kp = generate_keypair("ed25519")
    with Session(database.engine) as s:
        s.add(User(id=user_id, email="u@e.com", created_at="2026-01-01"))
        s.flush()  # ensure User row exists before FK reference
        s.add(
            SshKey(
                id=key_id,
                user_id=user_id,
                name="laptop",
                algorithm="ed25519",
                public_key=kp.public_key,
                private_key_enc=_cipher().encrypt(kp.private_key),
                fingerprint=kp.fingerprint,
                created_at="2026-01-01",
            )
        )
        s.commit()


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    porcelain.init(str(ws))
    (ws / "n.md").write_text("x")
    porcelain.add(str(ws), paths=["n.md"])
    porcelain.commit(str(ws), message=b"c", author=b"t <t@t>", committer=b"t <t@t>")
    return ws


def test_push_handler_pushes_and_marks_pushed(database, tmp_path):
    _seed_key(database)
    ws = _workspace(tmp_path)
    bare = tmp_path / "remote.git"
    porcelain.init(str(bare), bare=True)
    remotes = WorkspaceRemoteRepository(database.engine)
    remotes.upsert("u1", "ws", origin_url=str(bare), ssh_key_id="k1", enabled=True, now="t")

    _handler(database, tmp_path)({"user_id": "u1", "workspace": "ws", "ws_path": str(ws)})

    assert Repo(str(bare)).refs[b"refs/heads/master"] is not None  # ty: ignore[invalid-argument-type] - Literal[bytes] satisfies Dulwich Ref
    got = remotes.get("u1", "ws")
    assert got is not None
    assert got.pushed_at is not None and got.last_error is None
    # the temp key was cleaned up (no leftover .key files)
    assert not list(Path(tmp_path).glob("*.key"))


def test_push_handler_noop_when_disabled(database, tmp_path):
    _seed_key(database)
    ws = _workspace(tmp_path)
    remotes = WorkspaceRemoteRepository(database.engine)
    remotes.upsert("u1", "ws", origin_url="x", ssh_key_id="k1", enabled=False, now="t")
    # disabled -> no push attempt, no error
    _handler(database, tmp_path)({"user_id": "u1", "workspace": "ws", "ws_path": str(ws)})
    got = remotes.get("u1", "ws")
    assert got is not None
    assert got.pushed_at is None


def test_push_handler_failure_marks_and_raises(database, tmp_path):
    _seed_key(database)
    ws = _workspace(tmp_path)
    remotes = WorkspaceRemoteRepository(database.engine)
    remotes.upsert(
        "u1", "ws", origin_url=str(tmp_path / "nope"), ssh_key_id="k1", enabled=True, now="t"
    )
    with pytest.raises(GitError):
        _handler(database, tmp_path)({"user_id": "u1", "workspace": "ws", "ws_path": str(ws)})
    got = remotes.get("u1", "ws")
    assert got is not None
    assert got.last_error is not None
    assert not list(Path(tmp_path).glob("*.key"))  # key cleaned up even on failure
