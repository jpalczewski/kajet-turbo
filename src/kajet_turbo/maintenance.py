"""One-off maintenance routines. Run with ``uv run python -m kajet_turbo.maintenance``."""

import os
from pathlib import Path

from kajet_turbo.log import logger
from kajet_turbo.repositories.git import GitError, GitRepository


def migrate_workspaces_to_main(workspaces_dir: str) -> list[str]:
    """Rename every ``<dir>/<user>/<workspace>`` git repo from master to main.
    Idempotent; returns the workspace paths that were actually migrated."""
    base = Path(workspaces_dir)
    migrated: list[str] = []
    if not base.is_dir():
        return migrated
    for user_dir in sorted(base.iterdir()):
        if not user_dir.is_dir():
            continue
        for ws_dir in sorted(user_dir.iterdir()):
            if not (ws_dir / ".git").is_dir():
                continue
            try:
                if GitRepository(str(ws_dir)).rename_master_to_main():
                    migrated.append(str(ws_dir))
                    logger.info("workspace_migrated_to_main", workspace=str(ws_dir))
            except GitError as e:
                logger.warning("workspace_migration_failed", workspace=str(ws_dir), error=str(e))
    return migrated


if __name__ == "__main__":
    workspaces_dir = os.getenv("WORKSPACES_DIR", "/workspaces")
    result = migrate_workspaces_to_main(workspaces_dir)
    logger.info("migration_complete", count=len(result))
