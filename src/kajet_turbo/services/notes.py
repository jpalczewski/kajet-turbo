import json
import time
from datetime import UTC, datetime
from pathlib import Path

import frontmatter
from nanoid import generate

from kajet_turbo.repositories.git import GitError, GitRepository
from kajet_turbo.log import logger
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.workspace import normalize_folder, note_filepath, read_note_file, scan_notes, write_note_file


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
        folder: str = "",
    ) -> dict:
        folder = normalize_folder(folder)
        if not self._repo.check_unique(ws_name, user_id, folder, title):
            raise ValueError(f"Notatka '{title}' już istnieje w folderze '{folder or 'root'}'.")
        note_id = generate(size=7)
        now = datetime.now(UTC).isoformat()
        filepath = note_filepath(ws_path, folder, title)
        relative = str(Path(filepath).relative_to(ws_path))
        write_note_file(filepath, note_id, title, tags, now, now, content)
        try:
            GitRepository(ws_path).commit_file(relative, f"note: add {title}")
        except GitError:
            Path(filepath).unlink(missing_ok=True)
            raise
        self._repo.insert(note_id, ws_name, user_id, title, tags, now, now, content, folder)
        logger.info("note_saved", note_id=note_id, ws=ws_name, folder=folder)
        return {"note_id": note_id}

    def get(self, note_id: str, owner_id: str) -> dict | None:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        return {
            "note_id": note.id,
            "workspace": note.workspace,
            "owner_id": note.owner_id,
            "title": note.title,
            "folder": note.folder,
            "tags": json.loads(note.tags or "[]"),
            "created_at": note.created_at,
            "updated_at": note.updated_at,
        }

    def get_with_content(self, note_id: str, owner_id: str, ws_path: str) -> dict | None:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        filepath = note_filepath(ws_path, note.folder, note.title)
        if not Path(filepath).exists():
            return None
        note_data = read_note_file(filepath)
        return {
            "note_id": note.id,
            "workspace": note.workspace,
            "owner_id": note.owner_id,
            "title": note.title,
            "folder": note.folder,
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
        folder: str | None = None,
    ) -> dict:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        now = datetime.now(UTC).isoformat()
        new_title = title if title is not None else note.title
        new_folder = normalize_folder(folder) if folder is not None else note.folder
        current_tags = json.loads(note.tags or "[]")
        new_tags = tags if tags is not None else current_tags

        old_path = note_filepath(ws_path, note.folder, note.title)
        new_path = note_filepath(ws_path, new_folder, new_title)
        old_rel = str(Path(old_path).relative_to(ws_path))
        new_rel = str(Path(new_path).relative_to(ws_path))

        if not Path(old_path).exists():
            raise FileNotFoundError(f"Plik notatki {note_id} nie znaleziony.")
        note_data = read_note_file(old_path)
        old_content = note_data["content"]
        new_content = content if content is not None else old_content

        repo = GitRepository(ws_path)
        try:
            if old_path != new_path:
                Path(new_path).parent.mkdir(parents=True, exist_ok=True)
                repo.rename_file(old_rel, new_rel, f"note: rename to {new_title}")
                write_note_file(new_path, note_id, new_title, new_tags, note.created_at, now, new_content)
                repo.commit_file(new_rel, f"note: update {new_title}")
            else:
                write_note_file(old_path, note_id, new_title, new_tags, note.created_at, now, new_content)
                repo.commit_file(old_rel, f"note: update {new_title}")
        except GitError:
            write_note_file(
                new_path if old_path != new_path else old_path,
                note_id, note.title, current_tags, note.created_at, note.updated_at, old_content,
            )
            raise

        self._repo.update(
            note_id, owner_id=owner_id,
            title=new_title, content=new_content, tags=new_tags, updated_at=now, folder=new_folder,
        )
        logger.info("note_updated", note_id=note_id, folder=new_folder)
        return {"note_id": note_id}

    def delete(self, note_id: str, owner_id: str, ws_path: str) -> None:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        if Path(filepath).exists():
            relative = str(Path(filepath).relative_to(ws_path))
            GitRepository(ws_path).delete_file(relative, f"note: delete {note_id}")
        self._repo.delete(note_id, owner_id=owner_id)
        logger.info("note_deleted", note_id=note_id)

    def list(
        self,
        ws_name: str,
        owner_id: str,
        tags: list[str] | None = None,
        limit: int = 20,
        folder: str | None = None,
    ) -> list[dict]:
        return self._repo.list(ws_name, owner_id=owner_id, tags=tags, limit=limit, folder=folder)

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
        results = results[:limit]
        logger.info("search_performed", query_len=len(query), results=len(results), ws_count=len(workspaces))
        return results

    def reindex(self, ws_name: str, owner_id: str, ws_path: str) -> dict:
        start = time.monotonic()
        notes = scan_notes(ws_path)
        self._repo.delete_workspace_notes(ws_name, owner_id=owner_id)
        ws_root = Path(ws_path)
        count = 0
        for note in notes:
            if not note["id"]:
                continue
            filepath = Path(note["path"])
            rel_parent = filepath.relative_to(ws_root).parent
            folder = str(rel_parent).replace("\\", "/")
            if folder == ".":
                folder = ""
            self._repo.insert(
                note["id"], ws_name, owner_id,
                note["title"] or "", note["tags"] or [],
                str(note["created_at"] or ""), str(note["updated_at"] or ""),
                note["content"] or "", folder,
            )
            count += 1
        logger.info("reindex_complete", ws=ws_name, count=count,
                    duration_ms=round((time.monotonic() - start) * 1000))
        return {"message": f"Reindeksowano {count} notatek w workspace '{ws_name}'.", "count": count}

    def get_history(self, note_id: str, owner_id: str, ws_path: str, limit: int = 50) -> list[dict]:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        relative = str(Path(filepath).relative_to(ws_path))
        return GitRepository(ws_path).file_history(relative, limit=limit)

    def get_version(self, note_id: str, sha: str, owner_id: str, ws_path: str) -> dict:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        relative = str(Path(filepath).relative_to(ws_path))
        raw = GitRepository(ws_path).file_content_at_commit(relative, sha)
        parsed = frontmatter.loads(raw)
        return {
            "note_id": note_id,
            "workspace": note.workspace,
            "owner_id": note.owner_id,
            "title": str(parsed.get("title", note.title)),
            "folder": note.folder,
            "tags": list(parsed.get("tags", [])),
            "created_at": str(parsed.get("created_at", note.created_at)),
            "updated_at": str(parsed.get("updated_at", note.updated_at)),
            "content": parsed.content,
        }

    def restore_version(self, note_id: str, sha: str, owner_id: str, ws_path: str) -> dict:
        version = self.get_version(note_id, sha, owner_id, ws_path)
        return self.update(note_id, owner_id=owner_id, ws_path=ws_path, content=version["content"])
