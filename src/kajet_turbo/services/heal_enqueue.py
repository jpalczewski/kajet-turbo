"""Builds the post-commit hook that enqueues a workspace-wide dangling-link heal.
Resolves (user_id, workspace) from the committed path and enqueues only when the
workspace actually has dangling rows — so validation-on workspaces (which never
accumulate dangling links) pay nothing. Dedup is per-workspace, collapsing a burst
of commits into one pending heal."""

from collections.abc import Callable
from pathlib import Path

from kajet_turbo.log import logger
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.jobs import JobRepository


def make_enqueue_heal_on_commit(
    jobs: JobRepository,
    dangling: DanglingLinkRepository,
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
        if not dangling.exists(user_id, workspace):
            return  # nothing to heal — common path, zero job churn
        jobs.enqueue(
            "heal_dangling",
            {"user_id": user_id, "workspace": workspace},
            dedup_key=f"{user_id}:{workspace}",
            user_id=user_id,
        )
        logger.debug("heal_enqueued", workspace=workspace)

    return enqueue
