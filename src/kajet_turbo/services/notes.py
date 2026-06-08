import json
from datetime import UTC, datetime
from pathlib import Path

from nanoid import generate

from kajet_turbo.git_ops import GitError, commit_file, delete_file_commit
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.workspace import note_filepath, read_note_file, scan_notes, write_note_file


class NoteService:
    def __init__(self, note_repo: NoteRepository) -> None:
        self._repo = note_repo

    def save(
        self,
        user_id: str,
        ws_name: str,
        ws_path: str,
        title: str,
        content: str,
        tags: list[str],
    ) -> dict:
        note_id = generate(size=7)
        now = datetime.now(UTC).isoformat()
        filepath = note_filepath(ws_path, note_id, title)
        relative = str(Path(filepath).relative_to(ws_path))
        write_note_file(filepath, note_id, title, tags, now, now, content)
        try:
            commit_file(ws_path, relative, f"note: add {title}")
        except GitError:
            Path(filepath).unlink(missing_ok=True)
            raise
        self._repo.insert(note_id, ws_name, user_id, title, tags, now, now, content)
        return {"id": note_id}

    def get(self, note_id: str, owner_id: str) -> dict | None:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        # ws_path needed to find file — derive from note.workspace and env
        # caller must pass ws_path; accept it as optional to keep signature clean
        return {
            "id": note.id,
            "workspace": note.workspace,
            "owner_id": note.owner_id,
            "title": note.title,
            "tags": json.loads(note.tags or "[]"),
            "created_at": note.created_at,
            "updated_at": note.updated_at,
        }

    def get_with_content(self, note_id: str, owner_id: str, ws_path: str) -> dict | None:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        notes_dir = Path(ws_path) / "notes"
        files = list(notes_dir.glob(f"{note_id}-*.md"))
        if not files:
            return None
        note_data = read_note_file(str(files[0]))
        return {
            "id": note.id,
            "workspace": note.workspace,
            "owner_id": note.owner_id,
            "title": note.title,
            "tags": json.loads(note.tags or "[]"),
            "created_at": note.created_at,
            "updated_at": note.updated_at,
            "content": note_data["content"],
        }

    def update(
        self,
        note_id: str,
        owner_id: str,
        ws_path: str,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        now = datetime.now(UTC).isoformat()
        new_title = title if title is not None else note.title
        current_tags = json.loads(note.tags or "[]")
        new_tags = tags if tags is not None else current_tags
        notes_dir = Path(ws_path) / "notes"
        files = list(notes_dir.glob(f"{note_id}-*.md"))
        if not files:
            raise FileNotFoundError(f"Plik notatki {note_id} nie znaleziony.")
        note_data = read_note_file(str(files[0]))
        old_content = note_data["content"]
        new_content = content if content is not None else old_content
        write_note_file(str(files[0]), note_id, new_title, new_tags, note.created_at, now, new_content)
        try:
            relative = str(files[0].relative_to(ws_path))
            commit_file(ws_path, relative, f"note: update {new_title}")
        except GitError:
            write_note_file(str(files[0]), note_id, note.title, current_tags, note.created_at, note.updated_at, old_content)
            raise
        self._repo.update(note_id, owner_id=owner_id, title=new_title, content=new_content, tags=new_tags, updated_at=now)
        return {"id": note_id}

    def delete(self, note_id: str, owner_id: str, ws_path: str) -> None:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        notes_dir = Path(ws_path) / "notes"
        files = list(notes_dir.glob(f"{note_id}-*.md"))
        if files:
            relative = str(files[0].relative_to(ws_path))
            delete_file_commit(ws_path, relative, f"note: delete {note_id}")
        self._repo.delete(note_id, owner_id=owner_id)

    def list(
        self,
        ws_name: str,
        owner_id: str,
        tags: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict]:
        return self._repo.list(ws_name, owner_id=owner_id, tags=tags, limit=limit)

    def search(
        self,
        query: str,
        workspaces: list[str],
        owner_id: str,
        limit: int = 10,
    ) -> list[dict]:
        per_ws_limit = limit * 3 if len(workspaces) > 1 else limit
        results = []
        for ws in workspaces:
            hits = self._repo.hybrid_search(query, ws, owner_id, limit=per_ws_limit)
            results.extend(hits)
        return results[:limit]

    def reindex(self, ws_name: str, owner_id: str, ws_path: str) -> dict:
        notes = scan_notes(ws_path)
        self._repo.delete_workspace_notes(ws_name, owner_id=owner_id)
        for note in notes:
            if not note["id"]:
                continue
            self._repo.insert(
                note["id"],
                ws_name,
                owner_id,
                note["title"] or "",
                note["tags"] or [],
                str(note["created_at"] or ""),
                str(note["updated_at"] or ""),
                note["content"] or "",
            )
        return {"message": f"Reindeksowano {len(notes)} notatek w workspace '{ws_name}'.", "count": len(notes)}
