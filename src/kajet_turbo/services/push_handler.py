"""The ``push_workspace`` job handler: load a workspace's remote, decrypt its SSH
key to a transient 0600 tmpfs file, push, and record status. The plaintext key
exists only for the duration of the push and is always deleted."""

import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

from kajet_turbo.crypto import KeyCipher
from kajet_turbo.log import logger
from kajet_turbo.repositories.git_push import push as git_push
from kajet_turbo.repositories.ssh_keys import SshKeyRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository


class PushHandler:
    def __init__(
        self,
        remote_repo: WorkspaceRemoteRepository,
        ssh_key_repo: SshKeyRepository,
        cipher_factory: Callable[[], KeyCipher],
        *,
        known_hosts_path: str,
        key_dir: str,
    ):
        self._remotes = remote_repo
        self._keys = ssh_key_repo
        self._cipher_factory = cipher_factory
        self._known_hosts = known_hosts_path
        self._key_dir = key_dir

    def __call__(self, payload: dict) -> None:
        user_id, workspace, ws_path = (
            payload["user_id"],
            payload["workspace"],
            payload["ws_path"],
        )
        remote = self._remotes.get(user_id, workspace)
        if remote is None or not remote.enabled:
            return  # config removed/disabled since enqueue — nothing to do
        key = self._keys.get(user_id, remote.ssh_key_id)
        if key is None:
            # The key was deleted (shouldn't happen with ON DELETE RESTRICT, but be
            # defensive). Retrying won't help — record and stop, don't raise.
            self._remotes.mark_failed(user_id, workspace, "ssh key missing")
            return
        private = self._cipher_factory().decrypt(key.private_key_enc)
        Path(self._known_hosts).parent.mkdir(parents=True, exist_ok=True)
        key_path = self._write_key(private)
        try:
            git_push(ws_path, remote.origin_url, key_path, self._known_hosts)
        except Exception as e:
            logger.warning("push_failed", workspace=workspace, error=str(e))
            self._remotes.mark_failed(user_id, workspace, str(e))
            raise  # let the job retry with backoff
        else:
            self._remotes.mark_pushed(user_id, workspace)
        finally:
            Path(key_path).unlink()

    def _write_key(self, private: str) -> str:
        fd, path = tempfile.mkstemp(dir=self._key_dir, suffix=".key")
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — ssh refuses world-readable keys
        with os.fdopen(fd, "w") as f:
            f.write(private if private.endswith("\n") else private + "\n")
        return path
