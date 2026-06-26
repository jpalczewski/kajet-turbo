"""Per-workspace remote configuration + manual push trigger. Validates that the
chosen SSH key belongs to the user, and enqueues push jobs identically to the
commit hook (same dedup_key + ws_path) so manual and auto pushes coalesce."""

from kajet_turbo.models import WorkspaceRemote
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.ssh_keys import SshKeyRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
from kajet_turbo.workspace import workspace_path


class WorkspaceRemoteService:
    def __init__(
        self,
        remote_repo: WorkspaceRemoteRepository,
        ssh_key_repo: SshKeyRepository,
        job_repo: JobRepository,
        workspaces_dir: str,
    ):
        self._remotes = remote_repo
        self._keys = ssh_key_repo
        self._jobs = job_repo
        self._workspaces_dir = workspaces_dir

    @staticmethod
    def _view(r: WorkspaceRemote) -> dict:
        return {
            "origin_url": r.origin_url,
            "ssh_key_id": r.ssh_key_id,
            "enabled": r.enabled,
            "dirty_at": r.dirty_at,
            "pushed_at": r.pushed_at,
            "last_error": r.last_error,
        }

    def get(self, user_id: str, workspace: str) -> dict | None:
        row = self._remotes.get(user_id, workspace)
        return self._view(row) if row else None

    def set(
        self, user_id: str, workspace: str, *, origin_url: str, ssh_key_id: str, enabled: bool
    ) -> dict:
        origin_url = origin_url.strip()
        if not origin_url:
            raise ValueError("origin_url is required")
        if self._keys.get(user_id, ssh_key_id) is None:
            raise ValueError("ssh key not found")
        row = self._remotes.upsert(
            user_id, workspace, origin_url=origin_url, ssh_key_id=ssh_key_id, enabled=enabled
        )
        return self._view(row)

    def delete(self, user_id: str, workspace: str) -> bool:
        return self._remotes.delete(user_id, workspace)

    def trigger_push(self, user_id: str, workspace: str) -> bool:
        remote = self._remotes.get(user_id, workspace)
        if remote is None or not remote.enabled:
            return False
        ws_path = workspace_path(workspace, workspaces_dir=self._workspaces_dir, user_id=user_id)
        self._remotes.mark_dirty(user_id, workspace)
        self._jobs.enqueue(
            "push_workspace",
            {"user_id": user_id, "workspace": workspace, "ws_path": ws_path},
            dedup_key=f"{user_id}:{workspace}",
            user_id=user_id,
        )
        return True
