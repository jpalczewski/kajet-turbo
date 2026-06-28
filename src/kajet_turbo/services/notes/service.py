import json
import time
from datetime import UTC, datetime
from pathlib import Path

from nanoid import generate
from sqlmodel import Session

from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.log import logger
from kajet_turbo.markdown import (
    BrokenWikilinkError,
    apply_edit,
    extract_wikilinks,
    split_target,
)
from kajet_turbo.perf import timed
from kajet_turbo.repositories.git import GitError, GitRepository
from kajet_turbo.repositories.notes import (
    NoteChunkRepository,
    NoteLinkRepository,
    NoteRepository,
    NoteTagRepository,
)
from kajet_turbo.services.notes.folders import NoteFolderService
from kajet_turbo.services.notes.history import NoteVersionService
from kajet_turbo.services.notes.links import NoteLinkService
from kajet_turbo.services.notes.search import NoteSearchService
from kajet_turbo.services.notes.tags import NoteTagService
from kajet_turbo.services.notes.types import NoteData
from kajet_turbo.workspace import (
    InvalidFolderError,
    normalize_folder,
    note_filepath,
    read_note_file,
    scan_notes,
    write_note_file,
)


class NoteService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        link_repo: NoteLinkRepository,
        tag_repo: NoteTagRepository,
        chunk_repo: NoteChunkRepository,
        tag_service: NoteTagService,
        link_service: NoteLinkService,
        search_service: NoteSearchService,
        version_service: NoteVersionService,
        folder_service: NoteFolderService,
        indexer=None,
        cache: WorkspaceCache | None = None,
    ) -> None:
        self._crud_repo = crud_repo
        self._link_repo = link_repo
        self._tag_repo = tag_repo
        self._chunk_repo = chunk_repo
        self._tag_service = tag_service
        self._link_service = link_service
        self._search_service = search_service
        self._version_service = version_service
        self._folder_service = folder_service
        self._indexer = indexer
        self._cache = cache
        # shared engine for cross-repo atomic transactions (reindex)
        self._engine = crud_repo._engine if crud_repo is not None else None

    def _index(self, note_id: str, ws_name: str, owner_id: str, title: str, content: str) -> None:
        # Chunks + FTS are the reliable search backbone (written by replace_chunks inside
        # index_note); a real DB write error surfaces. Network embedding is best-effort and
        # is swallowed *inside* index_note, never here.
        if self._indexer is None:
            return
        self._indexer.index_note(note_id, ws_name, owner_id, title, content)

    def _clear_index(self, note_id: str) -> None:
        if self._indexer is None:
            return
        self._indexer.clear_note(note_id)

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
        if not self._crud_repo.check_unique(ws_name, user_id, folder, title):
            raise ValueError(f"Notatka '{title}' już istnieje w folderze '{folder or 'root'}'.")
        tags = NoteTagService.normalize_tags(tags)
        target_ids, broken_pairs = self._link_service.validate_wikilinks(ws_name, user_id, content)
        note_id = generate(size=7)
        now = datetime.now(UTC).isoformat()
        filepath = note_filepath(ws_path, folder, title)
        relative = str(Path(filepath).relative_to(ws_path))
        write_note_file(filepath, note_id, title, tags, now, now, content)
        try:
            with timed("git_ms"):
                GitRepository(ws_path).commit_file(relative, f"note: add {title}")
        except GitError:
            Path(filepath).unlink(missing_ok=True)
            raise
        with timed("db_ms"):
            self._crud_repo.insert(
                note_id, ws_name, user_id, title, tags, now, now, content, folder
            )
            self._link_repo.replace_links(note_id, ws_name, user_id, target_ids)
            self._tag_service.sync_tags(note_id, ws_name, user_id, tags, content)
        self._link_service.write_dangling(note_id, ws_name, user_id, broken_pairs)
        if self._cache is not None:
            self._cache.bump(ws_name, user_id)
        logger.info("note_saved", note_id=note_id, ws=ws_name, folder=folder)
        self._index(note_id, ws_name, user_id, title, content)
        return {"note_id": note_id}

    def save_many(
        self,
        user_id: str,
        ws_name: str,
        ws_path: str,
        notes: list[dict],
    ) -> list[dict]:
        """Create many notes in one batch: one git commit, one cache bump, embeddings
        parallelized across the indexer threadpool. Best-effort per note — invalid notes
        are reported and skipped. Each input dict: ``{title, content, tags=[], folder=""}``.
        Returns per-note ``{index, note_id}`` | ``{index, error}``, input order preserved.
        Raises GitError if the batch commit fails (written files are rolled back first).
        """
        results: list[dict | None] = [None] * len(notes)
        now = datetime.now(UTC).isoformat()

        # Phase 1: uniqueness + id assignment. Survivors get an id and register in the
        # batch target map so in-batch wikilinks resolve in Phase 2.
        accepted: set[tuple[str, str]] = set()
        accepted_paths: set[str] = set()
        batch_targets: dict[tuple[str, str], str] = {}
        survivors: list[dict] = []
        for index, raw in enumerate(notes):
            title = str(raw.get("title", "")).strip()
            if not title:
                results[index] = {"index": index, "error": "Tytuł jest wymagany."}
                continue
            folder = normalize_folder(str(raw.get("folder", "")))
            key = (folder, title)
            if key in accepted:
                results[index] = {
                    "index": index,
                    "error": f"Duplikat w batchu: '{title}' w folderze '{folder or 'root'}'.",
                }
                continue
            if not self._crud_repo.check_unique(ws_name, user_id, folder, title):
                results[index] = {
                    "index": index,
                    "error": f"Notatka '{title}' już istnieje w folderze '{folder or 'root'}'.",
                }
                continue
            note_id = generate(size=7)
            filepath = note_filepath(ws_path, folder, title)
            relative = str(Path(filepath).relative_to(ws_path))
            if relative in accepted_paths:
                results[index] = {
                    "index": index,
                    "error": f"Kolizja nazwy pliku z inną notatką w batchu: '{title}'.",
                }
                continue
            accepted.add(key)
            accepted_paths.add(relative)
            batch_targets[key] = note_id
            survivors.append(
                {
                    "index": index,
                    "note_id": note_id,
                    "title": title,
                    "content": str(raw.get("content", "")),
                    "tags": NoteTagService.normalize_tags(raw.get("tags", []) or []),
                    "folder": folder,
                    "filepath": filepath,
                    "relative": relative,
                }
            )

        # Phase 2: wikilink resolution against existing notes union batch_targets.
        # Non-cascading: batch_targets is not mutated as notes are dropped, so a link to a
        # later-dropped note still resolves (worst case a harmless orphan edge).
        valid: list[dict] = []
        for s in survivors:
            try:
                s["target_ids"], s["broken_pairs"] = self._link_service.validate_wikilinks(
                    ws_name, user_id, s["content"], extra_targets=batch_targets
                )
            except BrokenWikilinkError as e:
                results[s["index"]] = {"index": s["index"], "error": str(e)}
                continue
            valid.append(s)

        if not valid:
            return [r for r in results if r is not None]

        # Phase 3: write files, then one commit (roll back files on failure).
        for s in valid:
            write_note_file(
                s["filepath"], s["note_id"], s["title"], s["tags"], now, now, s["content"]
            )
        try:
            n = len(valid)
            GitRepository(ws_path).commit_files(
                [s["relative"] for s in valid], f"note: add {n} note{'' if n == 1 else 's'}"
            )
        except GitError:
            for s in valid:
                Path(s["filepath"]).unlink(missing_ok=True)
            raise

        # Phase 4: DB insert + link graph + tags.
        with timed("db_ms"):
            for s in valid:
                self._crud_repo.insert(
                    s["note_id"],
                    ws_name,
                    user_id,
                    s["title"],
                    s["tags"],
                    now,
                    now,
                    s["content"],
                    s["folder"],
                )
                self._link_repo.replace_links(s["note_id"], ws_name, user_id, s["target_ids"])
                self._tag_service.sync_tags(s["note_id"], ws_name, user_id, s["tags"], s["content"])
                self._link_service.write_dangling(s["note_id"], ws_name, user_id, s["broken_pairs"])

        if self._cache is not None:
            self._cache.bump(ws_name, user_id)

        # Phase 5: index — embedding parallelized across the threadpool, best-effort.
        if self._indexer is not None:
            self._indexer.index_many(
                ws_name,
                user_id,
                [{"id": s["note_id"], "title": s["title"], "content": s["content"]} for s in valid],
            )

        for s in valid:
            results[s["index"]] = {"index": s["index"], "note_id": s["note_id"]}
            logger.info("note_saved", note_id=s["note_id"], ws=ws_name, folder=s["folder"])

        return [r for r in results if r is not None]

    def get(self, note_id: str, owner_id: str) -> dict | None:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
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

    def get_with_content(self, note_id: str, owner_id: str, ws_path: str) -> NoteData | None:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        filepath = note_filepath(ws_path, note.folder, note.title)
        if not Path(filepath).exists():
            return None
        note_data = read_note_file(filepath)
        return NoteData(
            note_id=note.id,
            workspace=note.workspace,
            owner_id=note.owner_id,
            title=note.title,
            folder=note.folder,
            tags=json.loads(note.tags or "[]"),
            created_at=note.created_at,
            updated_at=note.updated_at,
            content=note_data["content"],
        )

    def preview_chunks(self, note_id: str, owner_id: str, ws_path: str) -> dict | None:
        """Live chunk preview for a note (reads current file content; never stored rows)."""
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        data = self.get_with_content(note_id, owner_id, ws_path)
        if data is None:
            return None
        chunks = self._indexer.preview(note.title, data.content, owner_id) if self._indexer else []
        return {
            "note_id": note.id,
            "title": note.title,
            "index_state": note.index_state,
            "chunk_count": len(chunks),
            "chunks": chunks,
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
        mode: str = "overwrite",
        target_heading: str | None = None,
        old_text: str | None = None,
        confirm: bool = False,
    ) -> dict:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        now = datetime.now(UTC).isoformat()
        new_title = title if title is not None else note.title
        try:
            new_folder = normalize_folder(folder) if folder is not None else note.folder
        except ValueError as e:
            raise InvalidFolderError(str(e)) from e
        current_tags = json.loads(note.tags or "[]")
        new_tags = NoteTagService.normalize_tags(tags) if tags is not None else current_tags

        old_path = note_filepath(ws_path, note.folder, note.title)
        new_path = note_filepath(ws_path, new_folder, new_title)
        old_rel = str(Path(old_path).relative_to(ws_path))
        new_rel = str(Path(new_path).relative_to(ws_path))

        if not Path(old_path).exists():
            raise FileNotFoundError(f"Plik notatki {note_id} nie znaleziony.")
        if old_path != new_path:
            if not self._crud_repo.check_unique(note.workspace, owner_id, new_folder, new_title):
                raise FileExistsError(
                    f"Notatka '{new_title}' już istnieje w folderze '{new_folder or 'root'}'."
                )
            if Path(new_path).exists():
                raise FileExistsError(f"Plik docelowy '{new_rel}' już istnieje.")
        note_data = read_note_file(old_path)
        old_content = note_data["content"]
        if mode == "overwrite":
            new_content = content if content is not None else old_content
        else:
            if content is None and mode not in ("replace_text", "delete_text"):
                raise ValueError("content jest wymagany dla trybu edycji.")
            new_content = apply_edit(old_content, mode, content or "", target_heading, old_text)

        # Validate links on the final content (post apply_edit), before any git mutation.
        target_ids, broken_pairs = self._link_service.validate_wikilinks(
            note.workspace, owner_id, new_content
        )

        old_fm_tags = NoteTagService.normalize_tags(note_data["tags"])
        would_remove = (
            [t for t in old_fm_tags if t not in set(new_tags)] if tags is not None else []
        )
        overwrites_content = (
            mode == "overwrite"
            and content is not None
            and old_content.strip() != ""
            and new_content != old_content
        )
        if (would_remove or overwrites_content) and not confirm:
            return NoteTagService._confirmation_payload(note_id, would_remove, overwrites_content)

        repo = GitRepository(ws_path)
        try:
            if old_path != new_path:
                Path(new_path).parent.mkdir(parents=True, exist_ok=True)
                repo.rename_file(old_rel, new_rel, f"note: rename to {new_title}")
                write_note_file(
                    new_path, note_id, new_title, new_tags, note.created_at, now, new_content
                )
                repo.commit_file(new_rel, f"note: update {new_title}")
            else:
                write_note_file(
                    old_path, note_id, new_title, new_tags, note.created_at, now, new_content
                )
                repo.commit_file(old_rel, f"note: update {new_title}")
        except GitError:
            write_note_file(
                new_path if old_path != new_path else old_path,
                note_id,
                note.title,
                current_tags,
                note.created_at,
                note.updated_at,
                old_content,
            )
            raise

        with timed("db_ms"):
            self._crud_repo.update(
                note_id,
                owner_id=owner_id,
                title=new_title,
                content=new_content,
                tags=new_tags,
                updated_at=now,
                folder=new_folder,
            )
            self._link_repo.replace_links(note_id, note.workspace, owner_id, target_ids)
            self._tag_service.sync_tags(note_id, note.workspace, owner_id, new_tags, new_content)
        self._link_service.write_dangling(note_id, note.workspace, owner_id, broken_pairs)
        if old_path != new_path:
            self._link_service.rewrite_backlinks(
                note_id, owner_id, ws_path, note.folder, note.title, new_folder, new_title
            )
        if self._cache is not None:
            self._cache.bump(note.workspace, owner_id)
        logger.info("note_updated", note_id=note_id, folder=new_folder)
        self._index(note_id, note.workspace, owner_id, new_title, new_content)
        return {"note_id": note_id}

    def delete(self, note_id: str, owner_id: str, ws_path: str) -> None:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        if Path(filepath).exists():
            relative = str(Path(filepath).relative_to(ws_path))
            GitRepository(ws_path).delete_file(relative, f"note: delete {note_id}")
        self._tag_repo.delete_note_tags(note_id, note.workspace, owner_id)
        self._clear_index(note_id)
        self._crud_repo.delete(note_id, owner_id=owner_id)
        self._link_repo.delete_links_from(note_id)
        self._link_repo.delete_links_to(note_id)
        self._link_service.delete_dangling_for_source(note_id)
        if self._cache is not None:
            self._cache.bump(note.workspace, owner_id)
        logger.info("note_deleted", note_id=note_id)

    def list_notes(
        self,
        ws_name: str,
        owner_id: str,
        tags: list[str] | None = None,
        limit: int | None = 20,
        folder: str | None = None,
        include_descendants: bool = True,
    ) -> list[dict]:
        return self._crud_repo.list_notes(
            ws_name,
            owner_id=owner_id,
            tags=tags,
            limit=limit,
            folder=folder,
            include_descendants=include_descendants,
            _tag_repo=self._tag_repo if tags else None,
        )

    def reindex(self, ws_name: str, owner_id: str, ws_path: str) -> dict:
        start = time.monotonic()
        notes = scan_notes(ws_path)
        self._tag_repo.delete_workspace_tags(ws_name, owner_id)
        # Atomic: chunk cleanup + note row deletion share one session.
        # FK ordering: chunks must be deleted before notes (note_chunks.note_id FK).
        with Session(self._engine) as session:
            self._chunk_repo.delete_for_workspace(ws_name, owner_id, session)
            self._crud_repo.delete_for_workspace(ws_name, owner_id, session)
            session.commit()
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
            self._crud_repo.insert(
                note["id"],
                ws_name,
                owner_id,
                note["title"] or "",
                note["tags"] or [],
                str(note["created_at"] or ""),
                str(note["updated_at"] or ""),
                note["content"] or "",
                folder,
            )
            count += 1
        self._link_repo.delete_workspace_links(ws_name, owner_id)
        for note in notes:
            if not note["id"]:
                continue
            fm_tags = NoteTagService.normalize_tags(note["tags"] or [])
            self._tag_service.sync_tags(
                note["id"], ws_name, owner_id, fm_tags, note["content"] or ""
            )
            pairs = [split_target(t) for t, _ in extract_wikilinks(note["content"] or "")]
            if not pairs:
                continue
            resolved = self._crud_repo.resolve_paths(ws_name, owner_id, pairs)
            if resolved:
                self._link_repo.replace_links(note["id"], ws_name, owner_id, set(resolved.values()))
        if self._indexer is not None:
            try:
                self._indexer.index_many(
                    ws_name,
                    owner_id,
                    [
                        {"id": n["id"], "title": n["title"] or "", "content": n["content"] or ""}
                        for n in notes
                        if n["id"]
                    ],
                )
            except Exception:
                logger.warning("reindex_chunk_index_failed", ws=ws_name)
        if self._cache is not None:
            self._cache.bump(ws_name, owner_id)
        logger.info(
            "reindex_complete",
            ws=ws_name,
            count=count,
            duration_ms=round((time.monotonic() - start) * 1000),
        )
        return {
            "message": f"Reindeksowano {count} notatek w workspace '{ws_name}'.",
            "count": count,
        }

    def restore_version(self, note_id: str, sha: str, owner_id: str, ws_path: str) -> dict:
        version = self._version_service.get_version(note_id, sha, owner_id, ws_path)
        # Version restore is an explicit intent — always bypass the destructive-op gate.
        return self.update(
            note_id, owner_id=owner_id, ws_path=ws_path, content=version["content"], confirm=True
        )

    # Delegation to peer services (public API unchanged):
    def backlinks(self, note_id: str, owner_id: str, include_meta: bool = False) -> list[dict]:
        return self._link_service.backlinks(note_id, owner_id, include_meta)

    def outlinks(self, note_id: str, owner_id: str, include_meta: bool = False) -> list[dict]:
        return self._link_service.outlinks(note_id, owner_id, include_meta)

    def links(self, note_id: str, owner_id: str, include_meta: bool = False) -> dict | None:
        return self._link_service.links(note_id, owner_id, include_meta)

    def link_resolver(self, ws_name: str, owner_id: str):
        return self._link_service.link_resolver(ws_name, owner_id)

    def xws_link_resolver(self, owner_id: str):
        return self._link_service.xws_link_resolver(owner_id)

    def add_tags(self, note_id: str, owner_id: str, ws_path: str, tags: list[str]) -> dict:
        return self._tag_service.add_tags(note_id, owner_id, ws_path, tags)

    def remove_tags(self, note_id: str, owner_id: str, ws_path: str, tags: list[str]) -> dict:
        return self._tag_service.remove_tags(note_id, owner_id, ws_path, tags)

    def set_tags(
        self, note_id: str, owner_id: str, ws_path: str, tags: list[str], confirm: bool = False
    ) -> dict:
        return self._tag_service.set_tags(note_id, owner_id, ws_path, tags, confirm)

    def tag_tree(self, ws_name: str, owner_id: str) -> list[dict]:
        return self._tag_repo.tag_tree(ws_name, owner_id)

    def tag_counts(
        self,
        ws_name: str,
        owner_id: str,
        folder: str | None = None,
        include_subfolders: bool = True,
    ) -> list[dict]:
        return self._tag_repo.tag_counts(ws_name, owner_id, folder, include_subfolders)

    def notes_by_tag(
        self,
        ws_name: str,
        owner_id: str,
        path: str,
        include_descendants: bool = True,
        limit: int | None = None,
    ) -> list[dict]:
        return self._tag_repo.notes_by_tag(ws_name, owner_id, path, include_descendants, limit)

    def search(
        self,
        query: str,
        workspaces: list[str],
        owner_id: str,
        limit: int = 10,
    ) -> list[dict]:
        return self._search_service.search(query, workspaces, owner_id, limit)

    def get_history(self, note_id: str, owner_id: str, ws_path: str, limit: int = 50) -> list[dict]:
        return self._version_service.get_history(note_id, owner_id, ws_path, limit)

    def get_version(self, note_id: str, sha: str, owner_id: str, ws_path: str) -> dict:
        return self._version_service.get_version(note_id, sha, owner_id, ws_path)

    def move(self, note_id: str, owner_id: str, ws_path: str, folder: str) -> dict:
        return self._folder_service.move(note_id, owner_id, ws_path, folder)

    def move_folder(
        self, src: str, dst: str, *, owner_id: str, ws_path: str, workspace: str
    ) -> dict:
        return self._folder_service.move_folder(
            src, dst, owner_id=owner_id, ws_path=ws_path, workspace=workspace
        )

    def prune_empty_folders(self, ws_path: str) -> dict:
        return self._folder_service.prune_empty_folders(ws_path)

    def list_folders(self, ws_path: str) -> list[str]:
        return self._folder_service.list_folders(ws_path)
