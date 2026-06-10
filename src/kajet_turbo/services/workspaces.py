from datetime import UTC, datetime

from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.workspace import create_workspace as _create_workspace
from kajet_turbo.workspace import list_workspaces as _list_workspaces
from kajet_turbo.workspace import workspace_path as _workspace_path


class WorkspaceService:
    def __init__(self, workspace_repo: WorkspaceRepository, note_repo: NoteRepository) -> None:
        self._repo = workspace_repo
        self._note_repo = note_repo

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
        if not names or not user_id:
            return [{"name": n, "file_count": 0, "last_commit_at": None} for n in names]
        stats = self._note_repo.workspace_stats(user_id, names)
        result = []
        for name in names:
            s = stats.get(name, {})
            last_updated = s.get("last_updated")
            if last_updated:
                last_commit_at = int(datetime.fromisoformat(last_updated).replace(tzinfo=UTC).timestamp())
            else:
                last_commit_at = None
            result.append({"name": name, "file_count": s.get("file_count", 0), "last_commit_at": last_commit_at})
        return result

    def has_access(self, user_id: str, name: str) -> bool:
        return self._repo.has_access(user_id, name)
