from pathlib import Path

from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.workspace import create_workspace as _create_workspace
from kajet_turbo.workspace import list_workspaces as _list_workspaces
from kajet_turbo.workspace import workspace_path as _workspace_path


class WorkspaceService:
    def __init__(self, workspace_repo: WorkspaceRepository) -> None:
        self._repo = workspace_repo

    def create(self, name: str, user_id: str | None) -> None:
        _create_workspace(name, user_id=user_id)
        if user_id:
            self._repo.grant_access(user_id, name)

    def workspace_path(self, user_id: str | None, name: str) -> str:
        return _workspace_path(name, user_id=user_id)

    def list_accessible(self, user_id: str | None) -> list[str]:
        if user_id:
            return self._repo.list_user_workspaces(user_id)
        return _list_workspaces()

    def list_with_details(self, user_id: str | None) -> list[dict]:
        names = self.list_accessible(user_id)
        result = []
        for name in names:
            ws_path = _workspace_path(name, user_id=user_id)
            file_count = sum(
                1 for p in Path(ws_path).rglob("*.md") if ".git" not in p.parts
            )
            try:
                last_commit_at = GitRepository(ws_path).last_commit_time()
            except Exception:
                last_commit_at = None
            result.append({"name": name, "file_count": file_count, "last_commit_at": last_commit_at})
        return result

    def has_access(self, user_id: str, name: str) -> bool:
        return self._repo.has_access(user_id, name)
