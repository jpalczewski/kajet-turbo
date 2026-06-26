"""Builds the post-commit hook that enqueues an auto-push. Resolves the
``(user_id, workspace)`` from the workspace path, and only enqueues when the
workspace has an enabled remote. The dedup_key is per-workspace, so a burst of
commits collapses to one pending push."""

from collections.abc import Callable
from pathlib import Path

from kajet_turbo.log import logger
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository


def make_enqueue_push_on_commit(
    jobs: JobRepository,
    remotes: WorkspaceRemoteRepository,
    workspaces_dir: str,
) -> Callable[[str], None]:
    base = Path(workspaces_dir).resolve()

    def enqueue(ws_path: str) -> None:
        try:
            rel = Path(ws_path).resolve().relative_to(base)
        except ValueError:
            return  # path not under WORKSPACES_DIR — not a user workspace
        if len(rel.parts) < 2:
            return
        user_id, workspace = rel.parts[0], rel.parts[1]
        remote = remotes.get(user_id, workspace)
        if remote is None or not remote.enabled:
            return
        remotes.mark_dirty(user_id, workspace)
        jobs.enqueue(
            "push_workspace",
            {"user_id": user_id, "workspace": workspace, "ws_path": ws_path},
            dedup_key=f"{user_id}:{workspace}",
            user_id=user_id,
        )
        logger.debug("push_enqueued", workspace=workspace)

    return enqueue
