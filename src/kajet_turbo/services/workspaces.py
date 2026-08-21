import json
from datetime import UTC, datetime

from kajet_turbo import workspace_settings
from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.log import logger
from kajet_turbo.markdown import tags as tagutil
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.workspace_meta import WorkspaceMetaRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.services.notes import NoteService
from kajet_turbo.workspace import create_workspace as _create_workspace
from kajet_turbo.workspace import delete_workspace_directory, normalize_folder
from kajet_turbo.workspace import workspace_path as _workspace_path

_EMPTY_META = {"description": "", "folder": "", "tags": []}


class WorkspaceService:
    def __init__(
        self,
        workspace_repo: WorkspaceRepository,
        note_repo: NoteRepository,
        meta_repo: WorkspaceMetaRepository,
        note_service: NoteService,
        dangling_repo: DanglingLinkRepository,
        folder_meta_repo: FolderMetaRepository,
        remote_repo: WorkspaceRemoteRepository,
        active_repo: ActiveWorkspaceRepository,
        cache: WorkspaceCache | None = None,
    ) -> None:
        self._repo = workspace_repo
        self._note_repo = note_repo
        self._meta_repo = meta_repo
        self._note_service = note_service
        self._dangling_repo = dangling_repo
        self._folder_meta_repo = folder_meta_repo
        self._remote_repo = remote_repo
        self._active_repo = active_repo
        self._cache = cache

    def create(self, name: str, user_id: str, *, description: str = "") -> None:
        _create_workspace(name, user_id=user_id)
        self._repo.grant_access(user_id, name)
        self._meta_repo.ensure(user_id, name)
        if description:
            self._meta_repo.set(user_id, name, description=description)

    def delete(self, user_id: str, name: str) -> None:
        """Wipes a workspace: every workspace-scoped DB row plus the on-disk git repo.

        Data tables go first and are all idempotent (no-op on 0 matching rows), so a
        delete that fails partway can simply be retried. workspace_access/workspace_meta
        go last, deliberately: as long as either row survives, has_access() still sees
        the workspace as present and the caller can retry the whole operation.
        """
        self._note_service.clear_workspace_data(name, user_id)
        self._dangling_repo.delete_for_workspace(user_id, name)
        self._folder_meta_repo.delete_for_workspace(user_id, name)
        self._remote_repo.delete(user_id, name)
        self._active_repo.delete_for_workspace(user_id, name)
        delete_workspace_directory(name, user_id=user_id)
        self._repo.revoke_access(user_id, name)
        self._meta_repo.delete(user_id, name)
        if self._cache is not None:
            self._cache.bump(name, user_id)
        logger.info("workspace_deleted", ws=name, owner_id=user_id)

    def workspace_path(self, user_id: str, name: str) -> str:
        return _workspace_path(name, user_id=user_id)

    def list_accessible(self, user_id: str) -> list[str]:
        return self._repo.list_user_workspaces(user_id)

    def set_meta(
        self,
        user_id: str,
        name: str,
        *,
        description: str | None = None,
        folder: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        norm_folder = normalize_folder(folder) if folder is not None else None
        norm_tags = None
        if tags is not None:
            normalized = []
            for raw in tags:
                t = tagutil.normalize(raw)
                if t is None:
                    raise ValueError(f"Invalid tag: {raw!r}")
                normalized.append(t)
            norm_tags = json.dumps(normalized)
        self._meta_repo.set(
            user_id, name, description=description, folder=norm_folder, tags=norm_tags
        )
        row = self._meta_repo.get(user_id, name)
        assert row is not None  # set() just upserted the row
        return row

    def list_meta(self, user_id: str) -> list[dict]:
        names = self.list_accessible(user_id)
        meta = self._meta_repo.get_many(user_id, names)
        return [{"name": n, **meta.get(n, _EMPTY_META)} for n in names]

    def list_with_details(self, user_id: str) -> list[dict]:
        names = self.list_accessible(user_id)
        stats = self._note_repo.workspace_stats(user_id, names)
        meta = self._meta_repo.get_many(user_id, names)
        result = []
        for name in names:
            s = stats.get(name, {})
            last_updated = s.get("last_updated")
            if last_updated:
                last_commit_at = int(
                    datetime.fromisoformat(last_updated).replace(tzinfo=UTC).timestamp()
                )
            else:
                last_commit_at = None
            result.append(
                {
                    "name": name,
                    "file_count": s.get("file_count", 0),
                    "last_commit_at": last_commit_at,
                    **meta.get(name, _EMPTY_META),
                }
            )
        return result

    def get_settings(self, user_id: str, name: str) -> dict:
        blob = self._meta_repo.get_settings(user_id, name)
        raw = json.loads(blob) if blob else None
        return workspace_settings.coerce_all(raw)

    def set_setting(self, user_id: str, name: str, key: str, value: object) -> dict:
        coerced = workspace_settings.validate(key, value)  # raises ValueError on bad key/type
        current = self.get_settings(user_id, name)
        current[key] = coerced
        self._meta_repo.set_settings(user_id, name, json.dumps(current))
        return current

    def has_access(self, user_id: str, name: str) -> bool:
        return self._repo.has_access(user_id, name)
