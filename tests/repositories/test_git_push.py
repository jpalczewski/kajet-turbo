from pathlib import Path

import pytest
from dulwich import porcelain
from dulwich.repo import Repo

from kajet_turbo.repositories.git import GitError
from kajet_turbo.repositories.git_push import build_ssh_command, current_branch, push


def _commit_workspace(path: Path) -> None:
    porcelain.init(str(path))
    (path / "note.md").write_text("hello")
    porcelain.add(str(path), paths=["note.md"])
    porcelain.commit(str(path), message=b"note: add", author=b"t <t@t>", committer=b"t <t@t>")


def test_build_ssh_command_has_tofu_and_known_hosts():
    cmd = build_ssh_command("/dev/shm/x.key", "/data/ssh/known_hosts")
    assert "-i /dev/shm/x.key" in cmd
    assert "StrictHostKeyChecking=accept-new" in cmd
    assert "UserKnownHostsFile=/data/ssh/known_hosts" in cmd
    assert "IdentitiesOnly=yes" in cmd


def test_current_branch_reads_head(tmp_path):
    ws = tmp_path / "ws"
    _commit_workspace(ws)
    assert current_branch(str(ws)) == b"refs/heads/master"


def test_push_to_local_bare_repo_transfers_commit(tmp_path):
    ws = tmp_path / "ws"
    _commit_workspace(ws)
    bare = tmp_path / "remote.git"
    porcelain.init(str(bare), bare=True)
    # Local-path remote: ssh_command is built but unused by the local transport;
    # this proves the refspec/object-transfer mechanics of push().
    push(str(ws), str(bare), "/dev/shm/unused.key", "/tmp/unused_known_hosts")
    remote = Repo(str(bare))
    remote_head = remote.refs[b"refs/heads/master"]  # ty: ignore[invalid-argument-type] - Literal[bytes] satisfies Dulwich Ref
    local_head = Repo(str(ws)).refs[b"refs/heads/master"]  # ty: ignore[invalid-argument-type] - Literal[bytes] satisfies Dulwich Ref
    assert remote_head == local_head


def test_push_rejected_raises_git_error(tmp_path):
    # Push to a path that is not a repo -> dulwich raises -> wrapped as GitError.
    ws = tmp_path / "ws"
    _commit_workspace(ws)
    with pytest.raises(GitError):
        push(str(ws), str(tmp_path / "does-not-exist"), "/dev/shm/x.key", "/tmp/kh")
