from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.workspace import create_workspace as _create_workspace


class WorkspaceService:
    def __init__(self, workspace_repo: WorkspaceRepository) -> None:
        self._repo = workspace_repo

    def create(self, name: str, user_id: str | None) -> None:
        _create_workspace(name, user_id=user_id)
        if user_id:
            self._repo.grant_access(user_id, name)

    def list_for_user(self, user_id: str) -> list[str]:
        return self._repo.list_user_workspaces(user_id)

    def has_access(self, user_id: str, name: str) -> bool:
        return self._repo.has_access(user_id, name)
