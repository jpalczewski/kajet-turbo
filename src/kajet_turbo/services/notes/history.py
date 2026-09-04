from pathlib import Path

import frontmatter

from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.workspace import note_filepath, parse_frontmatter


class NoteVersionService:
    def __init__(self, crud_repo: NoteRepository):
        self._crud_repo = crud_repo

    def get_history(self, note_id: str, owner_id: str, ws_path: str, limit: int = 50) -> list[dict]:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        relative = str(Path(filepath).relative_to(ws_path))
        return GitRepository(ws_path).file_history(relative, limit=limit)

    def get_version(self, note_id: str, sha: str, owner_id: str, ws_path: str) -> dict:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        relative = str(Path(filepath).relative_to(ws_path))
        raw = GitRepository(ws_path).file_content_at_commit(relative, sha)
        meta, content = parse_frontmatter(frontmatter.loads(raw))

        def or_default(value, default):
            return value if value is not None else default

        return {
            "note_id": note_id,
            "workspace": note.workspace,
            "owner_id": note.owner_id,
            "title": str(or_default(meta.title, note.title)),
            "folder": note.folder,
            "tags": meta.tags,
            "extras": meta.extras,
            "created_at": str(or_default(meta.created_at, note.created_at)),
            "updated_at": str(or_default(meta.updated_at, note.updated_at)),
            "occurred_at": meta.occurred_at,
            "period": meta.period,
            "content": content,
            "sha": sha,
        }
