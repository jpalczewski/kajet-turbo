from pathlib import Path

import frontmatter

from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.workspace import note_filepath


class NoteVersionService:
    def __init__(self, crud_repo: NoteRepository, cache: WorkspaceCache | None):
        self._crud_repo = crud_repo
        self._cache = cache

    def get_history(self, note_id: str, owner_id: str, ws_path: str, limit: int = 50) -> list[dict]:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        key = None
        if self._cache is not None:
            key = ("history", note_id, owner_id, self._cache.epoch(note.workspace, owner_id), limit)
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        filepath = note_filepath(ws_path, note.folder, note.title)
        relative = str(Path(filepath).relative_to(ws_path))
        entries = GitRepository(ws_path).file_history(relative, limit=limit)
        if self._cache is not None and key is not None:
            self._cache.put(key, entries)
        return entries

    def get_version(self, note_id: str, sha: str, owner_id: str, ws_path: str) -> dict:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        relative = str(Path(filepath).relative_to(ws_path))
        raw = GitRepository(ws_path).file_content_at_commit(relative, sha)
        parsed = frontmatter.loads(raw)
        # Frontmatter metadata is untyped; only a YAML list is a valid tags value.
        tags_meta = parsed.get("tags", [])
        return {
            "note_id": note_id,
            "workspace": note.workspace,
            "owner_id": note.owner_id,
            "title": str(parsed.get("title", note.title)),
            "folder": note.folder,
            "tags": list(tags_meta) if isinstance(tags_meta, list) else [],
            "created_at": str(parsed.get("created_at", note.created_at)),
            "updated_at": str(parsed.get("updated_at", note.updated_at)),
            "content": parsed.content,
        }
