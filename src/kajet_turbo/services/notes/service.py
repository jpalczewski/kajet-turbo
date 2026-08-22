import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import frontmatter
from nanoid import generate
from sqlmodel import Session

from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.log import logger
from kajet_turbo.markdown import (
    BrokenWikilinkError,
    IndexedNote,
    apply_edit,
    build_outline,
    join_target,
)
from kajet_turbo.models import Note
from kajet_turbo.perf import timed
from kajet_turbo.repositories.git import GitError, GitRepository
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.notes import (
    NoteChunkRepository,
    NoteLinkRepository,
    NoteRepository,
    NoteTagRepository,
    folder_sort_key,
)
from kajet_turbo.services.notes.folders import NoteFolderService
from kajet_turbo.services.notes.history import NoteVersionService
from kajet_turbo.services.notes.links import NoteLinkService, wikilink_warnings
from kajet_turbo.services.notes.search import NoteSearchService
from kajet_turbo.services.notes.staleness import (
    current_head_sha,
    sha_is_fresh,
    stale_error,
    stale_payload,
)
from kajet_turbo.services.notes.tags import NoteTagService
from kajet_turbo.services.notes.types import NoteData
from kajet_turbo.workspace import (
    InvalidFolderError,
    normalize_folder,
    note_filepath,
    note_folder,
    read_note_file,
    scan_notes,
    write_note_file,
)


@dataclass(frozen=True, slots=True)
class _LocatedNote:
    """A note row resolved to its workspace file, shared by batch pre-passes."""

    note: Note
    filepath: str
    relative: str
    file_exists: bool


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
        reconcile_repo: LinkReconcileRepository | None = None,
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
        self._reconcile_repo = reconcile_repo
        # shared engine for cross-repo atomic transactions (reindex)
        self._engine = crud_repo._engine if crud_repo is not None else None

    @staticmethod
    def _to_located(note: Note, ws_path: str) -> _LocatedNote:
        filepath = note_filepath(ws_path, note.folder, note.title)
        return _LocatedNote(
            note=note,
            filepath=filepath,
            relative=str(Path(filepath).relative_to(ws_path)),
            file_exists=Path(filepath).exists(),
        )

    def _locate(self, note_id: str, owner_id: str, ws_path: str) -> _LocatedNote | None:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        return self._to_located(note, ws_path)

    def _locate_batch(
        self, note_ids: list[str], owner_id: str, ws_path: str, git_repo: GitRepository
    ) -> tuple[dict[str, _LocatedNote], dict[str, str | None]]:
        """Resolve every note in a batch and compute all head shas in ONE git walk,
        before per-item validation. One DB query for all rows, one git walk for all
        head shas, instead of N of each. Only paths that exist on disk enter the
        walk — a nonexistent file has no sha to resolve and would force a
        full-history walk with no early exit.

        DB rows and Path.exists() are read once here instead of per-item inside the
        validation loop, which shifts the TOCTOU window slightly relative to a
        concurrent write landing mid-batch. This is a conscious, acceptable change:
        the sha-freshness check was already advisory outside the workspace lock
        (a commit only takes the lock in commit_files/delete_files), so the class of
        guarantee callers get is unchanged — only the window's exact size shifts.
        """
        stripped_ids = [n for raw_id in note_ids if (n := raw_id.strip())]
        notes = self._crud_repo.get_many(stripped_ids, owner_id)
        located = {note.id: self._to_located(note, ws_path) for note in notes}
        head_shas = git_repo.head_shas_for_paths(
            [loc.relative for loc in located.values() if loc.file_exists]
        )
        return located, head_shas

    def _note_data(self, loc: _LocatedNote, sha: str | None) -> NoteData:
        if sha is None:
            raise ValueError(
                f"Notatka {loc.note.id} nie ma historii commitów (niespójny stan repo)."
            )
        content = read_note_file(loc.filepath)["content"]
        return NoteData(
            note_id=loc.note.id,
            workspace=loc.note.workspace,
            owner_id=loc.note.owner_id,
            title=loc.note.title,
            folder=loc.note.folder,
            tags=json.loads(loc.note.tags or "[]"),
            created_at=loc.note.created_at,
            updated_at=loc.note.updated_at,
            content=content,
            sha=sha,
        )

    def _index(self, note_id: str, ws_name: str, owner_id: str, title: str, content: str) -> None:
        # Chunks + FTS are the reliable search backbone (written by replace_chunks inside
        # index_note); a real DB write error surfaces. The embedding HTTP roundtrip is
        # deferred to an embed_note job — index_note only enqueues, never hits the network.
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
        workspace_links = self._link_service.for_workspace(ws_name, user_id)
        links = workspace_links.validate(content, folder)
        affected_sources = workspace_links.affected_sources({title})
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
        self._crud_repo.insert(note_id, ws_name, user_id, title, tags, now, now, content, folder)
        self._link_service.persist(note_id, ws_name, user_id, links)
        self._tag_service.sync_tags(note_id, ws_name, user_id, tags, content)
        if self._cache is not None:
            self._cache.bump(ws_name, user_id)
        logger.info("note_saved", note_id=note_id, ws=ws_name, folder=folder)
        self._index(note_id, ws_name, user_id, title, content)
        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(user_id, ws_name, affected_sources)
        return {"note_id": note_id, "warnings": wikilink_warnings(links)}

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

        # Phase 1: uniqueness + id assignment. Survivors get an id and join the batch's
        # link index so in-batch wikilinks resolve in Phase 2.
        accepted: set[tuple[str, str]] = set()
        accepted_paths: set[str] = set()
        batch_notes: list[IndexedNote] = []
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
            batch_notes.append(IndexedNote(note_id, folder, title))
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

        # Phase 2: wikilink resolution against existing notes union the batch, sharing one
        # index. Non-cascading: the index is not rebuilt as notes are dropped, so a link to
        # a later-dropped note still resolves (worst case a harmless orphan edge).
        valid: list[dict] = []
        workspace_links = self._link_service.for_workspace(
            ws_name, user_id, extra=batch_notes
        )
        for s in survivors:
            try:
                s["links"] = workspace_links.validate(s["content"], s["folder"])
            except BrokenWikilinkError as e:
                results[s["index"]] = {"index": s["index"], "error": str(e)}
                continue
            valid.append(s)

        if not valid:
            return [r for r in results if r is not None]

        affected_sources = workspace_links.affected_sources(
            {str(s["title"]) for s in valid}
        )

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
        self._link_service.persist_many(
            ws_name,
            user_id,
            {str(s["note_id"]): s["links"] for s in valid},
        )
        for s in valid:
            self._tag_service.sync_tags(s["note_id"], ws_name, user_id, s["tags"], s["content"])

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
            results[s["index"]] = {
                "index": s["index"],
                "note_id": s["note_id"],
                "warnings": wikilink_warnings(s["links"]),
            }
            logger.info("note_saved", note_id=s["note_id"], ws=ws_name, folder=s["folder"])

        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(user_id, ws_name, affected_sources)

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
        """Single-note read. Batch reads go through ``get_many``/``_locate_batch``
        instead — a shared git walk beats N of these."""
        loc = self._locate(note_id, owner_id, ws_path)
        if loc is None or not loc.file_exists:
            return None
        return self._note_data(loc, current_head_sha(ws_path, loc.relative))

    def get_outline(self, note_id: str, owner_id: str, ws_path: str) -> dict | None:
        """Note structure (headings + section sizes) without content — for picking a
        target_heading before a surgical edit_note call without loading the full body."""
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        filepath = note_filepath(ws_path, note.folder, note.title)
        if not Path(filepath).exists():
            return None
        content = read_note_file(filepath)["content"]
        sections, preamble_chars, preamble_lines = build_outline(content)
        return {
            "note_id": note.id,
            "title": note.title,
            "folder": note.folder,
            "updated_at": note.updated_at,
            "total_chars": len(content),
            "total_lines": content.count("\n") + 1 if content else 0,
            "preamble_chars": preamble_chars,
            "preamble_lines": preamble_lines,
            "sections": [asdict(s) for s in sections],
        }

    def export_folder(
        self, ws_name: str, owner_id: str, ws_path: str, folder: str, max_chars: int = 80_000
    ) -> dict:
        """Concatenate a folder's subtree into one markdown document — for corpus-style
        reading (analyze N related notes as one document) instead of N separate get_note
        calls. Truncates at a note boundary once max_chars is exceeded; the first note is
        always included in full so a folder starting with one huge note never exports empty.
        """
        scope = normalize_folder(folder)
        notes = sorted(
            self._crud_repo.list_under_folder(ws_name, owner_id, scope), key=folder_sort_key
        )
        parts: list[str] = []
        omitted: list[dict] = []
        total_chars = 0
        truncated = False
        for index, note in enumerate(notes):
            filepath = note_filepath(ws_path, note.folder, note.title)
            if not Path(filepath).exists():
                continue
            content = read_note_file(filepath)["content"]
            heading_path = join_target(note.folder, note.title)
            tags = json.loads(note.tags or "[]")
            tag_line = f"_Tagi: {', '.join(tags)}_\n\n" if tags else ""
            section = f"# {heading_path}\n\n{tag_line}{content}".rstrip() + "\n"
            if index > 0 and not truncated and total_chars + len(section) > max_chars:
                truncated = True
            if truncated and index > 0:
                omitted.append({"note_id": note.id, "title": note.title, "chars": len(section)})
                continue
            parts.append(section)
            total_chars += len(section)
        markdown = "\n---\n\n".join(parts)
        logger.info(
            "folder_exported", ws=ws_name, folder=scope, note_count=len(parts), truncated=truncated
        )
        return {
            "markdown": markdown,
            "note_count": len(parts),
            "total_chars": len(markdown),
            "truncated": truncated,
            "omitted": omitted,
        }

    def get_many(self, note_ids: list[str], owner_id: str, ws_path: str) -> list[NoteData | dict]:
        """Read multiple notes in one call. Best-effort per id: a missing note becomes
        {"note_id": ..., "error": ...} instead of failing the whole call. Order-preserving.
        All head shas come from ONE shared git walk (head_shas_for_paths) instead of a
        per-note history walk — cost is the deepest note's history, not the sum."""
        git_repo = GitRepository(ws_path)
        located, head_shas = self._locate_batch(note_ids, owner_id, ws_path, git_repo)
        results: list[NoteData | dict] = []
        for note_id in note_ids:
            loc = located.get(note_id.strip())
            if loc is None or not loc.file_exists:
                results.append({"note_id": note_id, "error": f"Notatka {note_id} nie znaleziona."})
            else:
                results.append(self._note_data(loc, head_shas.get(loc.relative)))
        return results

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

    def grep(
        self,
        ws_name: str,
        ws_path: str,
        pattern: str,
        folder: str | None = None,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> dict:
        """Literal (non-semantic) substring search over raw note files, including
        frontmatter — for exact-text lookups (refactors, "is this word still anywhere")
        that search_notes' FTS/vector/metadata ranking cannot guarantee. Scoped to
        folder's subtree when given. Does not touch the DB; the workspace's files on
        disk are the source of truth for grep, same as scan_notes/reindex.
        """
        if not pattern.strip():
            raise ValueError("Wzorzec wyszukiwania nie może być pusty.")
        scope = normalize_folder(folder) if folder is not None else None
        ws_root = Path(ws_path)
        needle = pattern if case_sensitive else pattern.casefold()
        matches: list[dict] = []
        truncated = False
        for filepath in sorted(ws_root.rglob("*.md")):
            if ".git" in filepath.parts:
                continue
            folder = note_folder(ws_path, filepath)
            if scope is not None and not (folder == scope or folder.startswith(scope + "/")):
                continue
            raw = filepath.read_text(encoding="utf-8")
            post = frontmatter.loads(raw)
            note_id = str(post.get("id") or "")
            title = str(post.get("title") or "")
            # line_number is relative to the note body (what get_note returns as
            # `content`), since that's the only view an agent can act on — a raw
            # file line number would point at nothing in the API response. Offset
            # by the frontmatter block's line count; a match inside the frontmatter
            # itself (e.g. a tag) has no body line, so it reports 0.
            fm_offset = len(raw[: raw.rfind(post.content)].splitlines()) if post.content else 0
            for raw_line_number, line in enumerate(raw.splitlines(), start=1):
                haystack = line if case_sensitive else line.casefold()
                if needle not in haystack:
                    continue
                if len(matches) >= max_results:
                    truncated = True
                    break
                matches.append(
                    {
                        "note_id": note_id,
                        "title": title,
                        "folder": folder,
                        "line_number": max(0, raw_line_number - fm_offset),
                        "line": line,
                    }
                )
            if truncated:
                break
        logger.info("notes_grep", ws=ws_name, matches=len(matches), truncated=truncated)
        return {"matches": matches, "truncated": truncated}

    def update(
        self,
        note_id: str,
        owner_id: str,
        ws_path: str,
        expected_sha: str,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        folder: str | None = None,
        mode: str = "overwrite",
        target_heading: str | None = None,
        old_text: str | None = None,
        replace_all: bool = False,
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

        if not sha_is_fresh(current_head_sha(ws_path, old_rel), expected_sha):
            return stale_payload(note_id)

        if old_path != new_path:
            if not self._crud_repo.check_unique(note.workspace, owner_id, new_folder, new_title):
                raise FileExistsError(
                    f"Notatka '{new_title}' już istnieje w folderze '{new_folder or 'root'}'."
                )
            if Path(new_path).exists():
                raise FileExistsError(f"Plik docelowy '{new_rel}' już istnieje.")
        note_data = read_note_file(old_path)
        old_content = note_data["content"]
        if replace_all and mode not in ("replace_text", "delete_text"):
            raise ValueError("replace_all działa tylko z trybami 'replace_text'/'delete_text'.")
        replaced: int | None = None
        if mode == "overwrite":
            new_content = content if content is not None else old_content
        else:
            if content is None and mode != "delete_text":
                raise ValueError("content jest wymagany dla trybu edycji.")
            edit_result = apply_edit(
                old_content, mode, content or "", target_heading, old_text, replace_all=replace_all
            )
            new_content = edit_result.body
            replaced = edit_result.replaced

        # One pre-move snapshot serves validation and any backlink rewrite below.
        workspace_links = self._link_service.for_workspace(note.workspace, owner_id)
        links = workspace_links.validate(new_content, new_folder)
        identity_changed = note.title != new_title or note.folder != new_folder
        affected_sources = (
            workspace_links.affected_sources(
                {note.title, new_title}, include_source_ids={note_id}
            )
            if identity_changed
            else set()
        )

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

        self._crud_repo.update(
            note_id,
            owner_id=owner_id,
            title=new_title,
            content=new_content,
            tags=new_tags,
            updated_at=now,
            folder=new_folder,
        )
        self._link_service.persist(note_id, note.workspace, owner_id, links)
        self._tag_service.sync_tags(note_id, note.workspace, owner_id, new_tags, new_content)
        if old_path != new_path:
            move = (
                IndexedNote(note_id, note.folder, note.title),
                IndexedNote(note_id, new_folder, new_title),
            )
            workspace_links.rewrite_backlinks([move], ws_path)
        if self._cache is not None:
            self._cache.bump(note.workspace, owner_id)
        logger.info("note_updated", note_id=note_id, folder=new_folder)
        self._index(note_id, note.workspace, owner_id, new_title, new_content)
        if self._reconcile_repo is not None and identity_changed:
            self._reconcile_repo.mark_and_enqueue(
                owner_id, note.workspace, affected_sources
            )
        return {
            "note_id": note_id,
            "replaced": replaced,
            "warnings": wikilink_warnings(links),
        }

    def edit_many(
        self,
        user_id: str,
        ws_name: str,
        ws_path: str,
        edits: list[dict],
    ) -> dict:
        """Apply multiple surgical edits in ONE atomic commit. All-or-nothing at
        validation: any invalid edit (missing note, duplicate note_id, broken wikilink,
        bad anchor/heading) rejects the whole batch — nothing is written. Content + tags
        only; no title/folder changes (a rename needs backlink rewrites across other
        notes, incompatible with one commit_files call — use update() for that). Each
        input dict: {note_id, mode="append", content="", target_heading=None,
        old_text=None, replace_all=False, tags=None}.
        """
        if not edits:
            raise ValueError("Batch edycji nie może być pusty.")

        # One open repo for the whole batch: staleness checks during validation and
        # the single atomic commit afterwards, instead of re-opening per item.
        git_repo = GitRepository(ws_path)
        note_ids = [str(raw.get("note_id", "")).strip() for raw in edits]
        located, head_shas = self._locate_batch(note_ids, user_id, ws_path, git_repo)
        workspace_links = self._link_service.for_workspace(ws_name, user_id)
        seen_ids: set[str] = set()
        errors: list[dict] = []
        prepared: list[dict] = []
        for index, (raw, note_id) in enumerate(zip(edits, note_ids, strict=True)):
            if not note_id:
                errors.append(
                    {"index": index, "note_id": note_id, "error": "note_id jest wymagany."}
                )
                continue
            if note_id in seen_ids:
                errors.append(
                    {
                        "index": index,
                        "note_id": note_id,
                        "error": f"Duplikat note_id w batchu: '{note_id}'.",
                    }
                )
                continue
            seen_ids.add(note_id)
            loc = located.get(note_id)
            if loc is None:
                errors.append(
                    {
                        "index": index,
                        "note_id": note_id,
                        "error": f"Notatka {note_id} nie znaleziona.",
                    }
                )
                continue
            if not loc.file_exists:
                errors.append(
                    {
                        "index": index,
                        "note_id": note_id,
                        "error": f"Plik notatki {note_id} nie znaleziony.",
                    }
                )
                continue
            expected_sha = str(raw.get("expected_sha", "")).strip()
            if not expected_sha:
                errors.append(
                    {"index": index, "note_id": note_id, "error": "expected_sha jest wymagany."}
                )
                continue
            if not sha_is_fresh(head_shas.get(loc.relative), expected_sha):
                errors.append({"index": index, "note_id": note_id, "error": stale_error(note_id)})
                continue
            note_data = read_note_file(loc.filepath)
            old_content = note_data["content"]
            mode = raw.get("mode", "append")
            content = raw.get("content", "")
            target_heading = raw.get("target_heading")
            old_text = raw.get("old_text")
            replace_all = bool(raw.get("replace_all", False))
            try:
                edit_result = apply_edit(
                    old_content, mode, content, target_heading, old_text, replace_all=replace_all
                )
            except ValueError as e:
                errors.append({"index": index, "note_id": note_id, "error": str(e)})
                continue
            new_content = edit_result.body
            try:
                links = workspace_links.validate(new_content, loc.note.folder)
            except BrokenWikilinkError as e:
                errors.append({"index": index, "note_id": note_id, "error": str(e)})
                continue
            raw_tags = raw.get("tags")
            current_tags = NoteTagService.normalize_tags(note_data["tags"])
            new_tags = (
                NoteTagService.normalize_tags(raw_tags) if raw_tags is not None else current_tags
            )
            prepared.append(
                {
                    "index": index,
                    "note_id": note_id,
                    "note": loc.note,
                    "filepath": loc.filepath,
                    "relative": loc.relative,
                    "old_content": old_content,
                    "old_tags": current_tags,
                    "new_content": new_content,
                    "new_tags": new_tags,
                    "links": links,
                    "replaced": edit_result.replaced,
                }
            )

        if errors:
            return {"applied": False, "errors": errors}

        now = datetime.now(UTC).isoformat()
        for p in prepared:
            write_note_file(
                p["filepath"],
                p["note_id"],
                p["note"].title,
                p["new_tags"],
                p["note"].created_at,
                now,
                p["new_content"],
            )
        try:
            n = len(prepared)
            git_repo.commit_files(
                [p["relative"] for p in prepared], f"note: edit {n} note{'' if n == 1 else 's'}"
            )
        except GitError:
            for p in prepared:
                write_note_file(
                    p["filepath"],
                    p["note_id"],
                    p["note"].title,
                    p["old_tags"],
                    p["note"].created_at,
                    p["note"].updated_at,
                    p["old_content"],
                )
            raise

        for p in prepared:
            self._crud_repo.update(
                p["note_id"],
                owner_id=user_id,
                title=p["note"].title,
                content=p["new_content"],
                tags=p["new_tags"],
                updated_at=now,
                folder=p["note"].folder,
            )
        self._link_service.persist_many(
            ws_name,
            user_id,
            {str(p["note_id"]): p["links"] for p in prepared},
        )
        for p in prepared:
            self._tag_service.sync_tags(
                p["note_id"], ws_name, user_id, p["new_tags"], p["new_content"]
            )

        if self._cache is not None:
            self._cache.bump(ws_name, user_id)

        if self._indexer is not None:
            self._indexer.index_many(
                ws_name,
                user_id,
                [
                    {"id": p["note_id"], "title": p["note"].title, "content": p["new_content"]}
                    for p in prepared
                ],
            )

        results = [
            {
                "index": p["index"],
                "note_id": p["note_id"],
                "replaced": p["replaced"],
                "warnings": wikilink_warnings(p["links"]),
            }
            for p in prepared
        ]
        for p in prepared:
            logger.info("note_updated", note_id=p["note_id"], folder=p["note"].folder)
        logger.info("notes_edited_batch", ws=ws_name, count=len(prepared))
        return {"applied": True, "results": results}

    def delete(
        self, note_id: str, owner_id: str, ws_path: str, expected_sha: str | None = None
    ) -> dict:
        """Delete a note. expected_sha (MCP callers) must match the note's HEAD
        commit; ``None`` (REST API) skips the check. A missing file (orphaned DB
        row) also skips it — there is no version the caller could have read, and
        the delete is then pure index cleanup."""
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        file_exists = Path(filepath).exists()
        relative = ""
        if file_exists:
            relative = str(Path(filepath).relative_to(ws_path))
            if expected_sha is not None and not sha_is_fresh(
                current_head_sha(ws_path, relative), expected_sha
            ):
                return stale_payload(note_id)
        workspace_links = self._link_service.for_workspace(note.workspace, owner_id)
        affected_sources = workspace_links.affected_sources({note.title})
        affected_sources.discard(note_id)  # this source is synchronously deleted below
        if file_exists:
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
        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(
                owner_id, note.workspace, affected_sources
            )
        return {"note_id": note_id}

    def delete_many(
        self,
        user_id: str,
        ws_name: str,
        ws_path: str,
        deletes: list[dict],
    ) -> dict:
        """Delete multiple notes in ONE atomic commit. All-or-nothing: any invalid
        item (missing note, duplicate note_id, stale expected_sha) rejects the whole
        batch — nothing is deleted. Each input dict: {note_id, expected_sha} — sha is
        the note's current HEAD commit sha (from get_history), proving the caller has
        seen the version it is about to destroy; a mismatch rejects the item without
        revealing the current sha, forcing a real re-read instead of a blind retry.
        """
        if not deletes:
            raise ValueError("Batch usuwania nie może być pusty.")

        # One open repo for the whole batch: staleness checks during validation and
        # the single atomic commit afterwards, instead of re-opening per item.
        git_repo = GitRepository(ws_path)
        note_ids = [str(raw.get("note_id", "")).strip() for raw in deletes]
        located, head_shas = self._locate_batch(note_ids, user_id, ws_path, git_repo)
        seen_ids: set[str] = set()
        errors: list[dict] = []
        prepared: list[dict] = []
        for index, (raw, note_id) in enumerate(zip(deletes, note_ids, strict=True)):
            if not note_id:
                errors.append(
                    {"index": index, "note_id": note_id, "error": "note_id jest wymagany."}
                )
                continue
            if note_id in seen_ids:
                errors.append(
                    {
                        "index": index,
                        "note_id": note_id,
                        "error": f"Duplikat note_id w batchu: '{note_id}'.",
                    }
                )
                continue
            seen_ids.add(note_id)
            loc = located.get(note_id)
            if loc is None:
                errors.append(
                    {
                        "index": index,
                        "note_id": note_id,
                        "error": f"Notatka {note_id} nie znaleziona.",
                    }
                )
                continue
            if not loc.file_exists:
                errors.append(
                    {
                        "index": index,
                        "note_id": note_id,
                        "error": f"Plik notatki {note_id} nie znaleziony.",
                    }
                )
                continue
            expected_sha = str(raw.get("expected_sha", "")).strip()
            if not expected_sha:
                errors.append(
                    {"index": index, "note_id": note_id, "error": "expected_sha jest wymagany."}
                )
                continue
            if not sha_is_fresh(head_shas.get(loc.relative), expected_sha):
                errors.append({"index": index, "note_id": note_id, "error": stale_error(note_id)})
                continue
            prepared.append(
                {"index": index, "note_id": note_id, "note": loc.note, "relative": loc.relative}
            )

        if errors:
            return {"applied": False, "errors": errors}

        workspace_links = self._link_service.for_workspace(ws_name, user_id)
        affected_sources = workspace_links.affected_sources(
            {str(p["note"].title) for p in prepared}
        )
        affected_sources.difference_update(str(p["note_id"]) for p in prepared)

        n = len(prepared)
        git_repo.delete_files(
            [p["relative"] for p in prepared], f"note: delete {n} note{'' if n == 1 else 's'}"
        )

        for p in prepared:
            note_id = p["note_id"]
            note = p["note"]
            self._tag_repo.delete_note_tags(note_id, note.workspace, user_id)
            self._clear_index(note_id)
            self._crud_repo.delete(note_id, owner_id=user_id)
            self._link_repo.delete_links_from(note_id)
            self._link_repo.delete_links_to(note_id)
            self._link_service.delete_dangling_for_source(note_id)
            logger.info("note_deleted", note_id=note_id)

        if self._cache is not None:
            self._cache.bump(ws_name, user_id)
        logger.info("notes_deleted_batch", ws=ws_name, count=len(prepared))
        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(user_id, ws_name, affected_sources)
        results = [{"index": p["index"], "note_id": p["note_id"]} for p in prepared]
        return {"applied": True, "results": results}

    def list_notes(
        self,
        ws_name: str,
        owner_id: str,
        tags: list[str] | None = None,
        limit: int | None = 20,
        folder: str | None = None,
        include_descendants: bool = True,
        sort: str = "default",
    ) -> list[dict]:
        return self._crud_repo.list_notes(
            ws_name,
            owner_id=owner_id,
            tags=tags,
            limit=limit,
            folder=folder,
            include_descendants=include_descendants,
            sort=sort,
            _tag_repo=self._tag_repo if tags else None,
        )

    def clear_workspace_data(self, ws_name: str, owner_id: str) -> None:
        """Delete every note-related row for a workspace: tags, chunks (+ FTS/vec),
        notes, and links. Used by reindex (before rescanning) and by workspace deletion."""
        self._tag_repo.delete_workspace_tags(ws_name, owner_id)
        # Atomic: chunk cleanup + note row deletion share one session.
        # FK ordering: chunks must be deleted before notes (note_chunks.note_id FK).
        with Session(self._engine) as session, timed("db_ms"):
            self._chunk_repo.delete_for_workspace(ws_name, owner_id, session)
            self._crud_repo.delete_for_workspace(ws_name, owner_id, session)
            session.commit()
        self._link_repo.delete_workspace_links(ws_name, owner_id)
        self._link_service.delete_dangling_for_workspace(ws_name, owner_id)
        logger.info("workspace_data_cleared", ws=ws_name, owner_id=owner_id)

    def reindex(self, ws_name: str, owner_id: str, ws_path: str) -> dict:
        start = time.monotonic()
        notes = [n for n in scan_notes(ws_path) if n["id"]]
        self.clear_workspace_data(ws_name, owner_id)
        for note in notes:
            self._crud_repo.insert(
                note["id"],
                ws_name,
                owner_id,
                note["title"] or "",
                note["tags"] or [],
                str(note["created_at"] or ""),
                str(note["updated_at"] or ""),
                note["content"] or "",
                note["folder"],
            )
        # Link graph and dangling rows are rebuilt with the same resolution as save-time
        # validation (short titles, suffix paths, cross-workspace ids) against one index.
        workspace_links = self._link_service.for_workspace(ws_name, owner_id)
        resolutions = {}
        for note in notes:
            content = note["content"] or ""
            fm_tags = NoteTagService.normalize_tags(note["tags"] or [])
            self._tag_service.sync_tags(note["id"], ws_name, owner_id, fm_tags, content)
            resolutions[note["id"]] = workspace_links.resolve(content, note["folder"])
        self._link_service.persist_many(ws_name, owner_id, resolutions)
        if self._indexer is not None:
            try:
                self._indexer.index_many(
                    ws_name,
                    owner_id,
                    [
                        {"id": n["id"], "title": n["title"] or "", "content": n["content"] or ""}
                        for n in notes
                    ],
                )
            except Exception as e:
                logger.opt(exception=e).warning("reindex_chunk_index_failed", ws=ws_name)
        if self._cache is not None:
            self._cache.bump(ws_name, owner_id)
        logger.info(
            "reindex_complete",
            ws=ws_name,
            count=len(notes),
            duration_ms=round((time.monotonic() - start) * 1000),
        )
        return {
            "message": f"Reindeksowano {len(notes)} notatek w workspace '{ws_name}'.",
            "count": len(notes),
        }

    def restore_version(
        self,
        note_id: str,
        sha: str,
        owner_id: str,
        ws_path: str,
        expected_sha: str | None = None,
    ) -> dict:
        """Restore a past version over HEAD. expected_sha (MCP callers) proves the
        caller saw the HEAD it is about to overwrite; ``None`` (REST API) skips it."""
        version = self._version_service.get_version(note_id, sha, owner_id, ws_path)
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        relative = str(Path(note_filepath(ws_path, note.folder, note.title)).relative_to(ws_path))
        current_sha = current_head_sha(ws_path, relative)
        if current_sha is None:
            raise ValueError(f"Notatka {note_id} nie ma historii commitów.")
        if expected_sha is not None and not sha_is_fresh(current_sha, expected_sha):
            return stale_payload(note_id)
        # Restore proves intent by construction: update()'s own staleness check is
        # satisfied with the head sha just read.
        return self.update(
            note_id,
            owner_id=owner_id,
            ws_path=ws_path,
            expected_sha=current_sha,
            content=version["content"],
        )

    # Delegation to peer services (public API unchanged):
    def backlinks(self, note_id: str, owner_id: str, include_meta: bool = False) -> list[dict]:
        return self._link_service.backlinks(note_id, owner_id, include_meta)

    def outlinks(self, note_id: str, owner_id: str, include_meta: bool = False) -> list[dict]:
        return self._link_service.outlinks(note_id, owner_id, include_meta)

    def links(
        self,
        note_id: str,
        owner_id: str,
        include_meta: bool = False,
        include_cross_workspace: bool = True,
    ) -> dict | None:
        return self._link_service.links(note_id, owner_id, include_meta, include_cross_workspace)

    def link_resolver(self, ws_name: str, owner_id: str, source_folder: str = ""):
        return self._link_service.for_workspace(ws_name, owner_id).resolver(source_folder)

    def xws_link_resolver(self, owner_id: str):
        return self._link_service.xws_link_resolver(owner_id)

    def add_tags(self, note_id: str, owner_id: str, ws_path: str, tags: list[str]) -> dict:
        return self._tag_service.add_tags(note_id, owner_id, ws_path, tags)

    def remove_tags(self, note_id: str, owner_id: str, ws_path: str, tags: list[str]) -> dict:
        return self._tag_service.remove_tags(note_id, owner_id, ws_path, tags)

    def set_tags(
        self,
        note_id: str,
        owner_id: str,
        ws_path: str,
        tags: list[str],
        expected_sha: str | None = None,
    ) -> dict:
        return self._tag_service.set_tags(note_id, owner_id, ws_path, tags, expected_sha)

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
        folder: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        return self._search_service.search(
            query, workspaces, owner_id, limit, folder=folder, tags=tags
        )

    async def search_async(
        self,
        query: str,
        workspaces: list[str],
        owner_id: str,
        limit: int = 10,
        folder: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        return await self._search_service.search_async(
            query, workspaces, owner_id, limit, folder=folder, tags=tags
        )

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
