# `builtins.list` is used in annotations because the public `list()` method
# below shadows the `list` builtin within the class body.
import asyncio
import builtins
import json
import shutil
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from secrets import token_hex

import frontmatter
from nanoid import generate

from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.embedding.cache import pack_vector
from kajet_turbo.log import logger
from kajet_turbo.markdown import (
    BrokenWikilinkError,
    apply_edit,
    extract_inline_tags,
    extract_wikilinks,
    normalize,
    rewrite_wikilink_target,
    split_target,
)
from kajet_turbo.perf import timed
from kajet_turbo.repositories.git import GitError, GitRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.workspace import (
    InvalidFolderError,
    list_workspace_folders,
    normalize_folder,
    note_filepath,
    prune_all_empty_dirs,
    prune_empty_parents,
    read_note_file,
    remove_empty_tree,
    scan_notes,
    write_note_file,
)


class _ConfirmationRequired(Exception):
    """Internal signal: a destructive change needs confirmation. Carries the payload."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        super().__init__("confirmation required")


class NoteService:
    def __init__(
        self,
        note_repo: NoteRepository,
        cache: WorkspaceCache | None = None,
        indexer=None,
        query_resolver=None,
        build_embedder=None,
        query_cache=None,
    ) -> None:
        self._repo = note_repo
        self._cache = cache
        self._indexer = indexer
        self._query_resolver = query_resolver
        self._build_embedder = build_embedder
        self._query_cache = query_cache

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

    def _validate_wikilinks(
        self,
        ws_name: str,
        owner_id: str,
        content: str,
        extra_targets: dict[tuple[str, str], str] | None = None,
    ) -> set[str]:
        """Resolve every wikilink in ``content``; raise if any points to a missing note.

        ``extra_targets`` maps ``(folder, title) -> note_id`` for notes created in the
        same batch that are not yet persisted, so in-batch links resolve before insert.
        Returns the set of resolved target note_ids (used to persist the link graph).
        """
        pairs = [(target, split_target(target)) for target, _ in extract_wikilinks(content)]
        if not pairs:
            return set()
        resolved = self._repo.resolve_paths(ws_name, owner_id, [pair for _, pair in pairs])
        if extra_targets:
            for _, pair in pairs:
                if pair not in resolved and pair in extra_targets:
                    resolved[pair] = extra_targets[pair]
        broken = sorted({target for target, pair in pairs if pair not in resolved})
        if broken:
            raise BrokenWikilinkError(broken)
        return set(resolved.values())

    @staticmethod
    def _normalize_tags(raw: builtins.list[str]) -> builtins.list[str]:
        """Normalize frontmatter tags, dropping invalids and duplicates (order kept)."""
        out: builtins.list[str] = []
        seen: builtins.set[str] = set()
        for tag in raw:
            norm = normalize(tag)
            if norm and norm not in seen:
                seen.add(norm)
                out.append(norm)
        return out

    @staticmethod
    def _normalize_with_warnings(
        raw: builtins.list[str],
    ) -> tuple[builtins.list[str], builtins.list[str]]:
        """Like ``_normalize_tags`` but returns (normalized_unique, warnings).

        Invalid entries are reported as warnings instead of being silently dropped,
        so a batch tool can surface them without failing the whole call.
        """
        out: builtins.list[str] = []
        seen: builtins.set[str] = set()
        warnings: builtins.list[str] = []
        for tag in raw:
            norm = normalize(tag)
            if norm is None:
                warnings.append(f"{tag!r}: niepoprawny tag — pominięty")
                continue
            if norm not in seen:
                seen.add(norm)
                out.append(norm)
        return out, warnings

    def _sync_tags(
        self, note_id: str, ws_name: str, owner_id: str, fm_tags: builtins.list[str], content: str
    ) -> None:
        """Index the note's tags: union of frontmatter (normalized) and inline, frontmatter wins."""
        tagged: dict[str, str] = dict.fromkeys(fm_tags, "frontmatter")
        for tag in extract_inline_tags(content):
            tagged.setdefault(tag, "inline")
        self._repo.sync_note_tags(note_id, ws_name, owner_id, list(tagged.items()))

    def _rewrite_backlinks(
        self,
        note_id: str,
        owner_id: str,
        ws_path: str,
        old_folder: str,
        old_title: str,
        new_folder: str,
        new_title: str,
    ) -> None:
        """Rewrite wikilink paths in every note that links to ``note_id`` after it moved/renamed.

        Each affected source is committed separately; the link graph edges are unchanged
        (``target_note_id`` stays the same), so no link-table update is needed.
        """
        source_ids = self._repo.backlinks(note_id)
        if not source_ids:
            return
        old_key = (old_folder, old_title)
        new_target = f"{new_folder}/{new_title}" if new_folder else new_title
        repo = GitRepository(ws_path)
        for source_id in source_ids:
            src = self._repo.get(source_id, owner_id=owner_id)
            if src is None:
                continue
            src_path = note_filepath(ws_path, src.folder, src.title)
            if not Path(src_path).exists():
                continue
            data = read_note_file(src_path)
            new_body, changed = rewrite_wikilink_target(data["content"], old_key, new_target)
            if not changed:
                continue
            write_note_file(
                src_path,
                src.id,
                src.title,
                json.loads(src.tags or "[]"),
                src.created_at,
                src.updated_at,
                new_body,
            )
            relative = str(Path(src_path).relative_to(ws_path))
            repo.commit_file(relative, f"note: rewrite wikilink {old_title} -> {new_title}")
            self._repo.update(
                source_id, owner_id=owner_id, content=new_body, updated_at=src.updated_at
            )

    def save(
        self,
        user_id: str,
        ws_name: str,
        ws_path: str,
        title: str,
        content: str,
        tags: builtins.list[str],
        folder: str = "",
    ) -> dict:
        folder = normalize_folder(folder)
        if not self._repo.check_unique(ws_name, user_id, folder, title):
            raise ValueError(f"Notatka '{title}' już istnieje w folderze '{folder or 'root'}'.")
        tags = self._normalize_tags(tags)
        target_ids = self._validate_wikilinks(ws_name, user_id, content)
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
        with timed("db_ms"):
            self._repo.insert(note_id, ws_name, user_id, title, tags, now, now, content, folder)
            self._repo.replace_links(note_id, ws_name, user_id, target_ids)
            self._sync_tags(note_id, ws_name, user_id, tags, content)
        if self._cache is not None:
            self._cache.bump(ws_name, user_id)
        logger.info("note_saved", note_id=note_id, ws=ws_name, folder=folder)
        self._index(note_id, ws_name, user_id, title, content)
        return {"note_id": note_id}

    def _resolve_link_notes(
        self, note_ids: builtins.list[str], owner_id: str
    ) -> builtins.list[dict]:
        """Map note_ids to ``{note_id, title, folder}``, skipping missing/foreign notes."""
        result = []
        for note_id in note_ids:
            note = self._repo.get(note_id, owner_id=owner_id)
            if note is not None:
                result.append({"note_id": note.id, "title": note.title, "folder": note.folder})
        return result

    def backlinks(self, note_id: str, owner_id: str) -> builtins.list[dict]:
        """Notes that link to ``note_id``. Orphaned/foreign sources are skipped."""
        return self._resolve_link_notes(self._repo.backlinks(note_id), owner_id)

    def outlinks(self, note_id: str, owner_id: str) -> builtins.list[dict]:
        """Notes that ``note_id`` links to. Orphaned/foreign targets are skipped."""
        return self._resolve_link_notes(self._repo.outlinks(note_id), owner_id)

    def links(self, note_id: str, owner_id: str) -> dict:
        return {
            "backlinks": self.backlinks(note_id, owner_id),
            "outlinks": self.outlinks(note_id, owner_id),
        }

    def link_resolver(self, ws_name: str, owner_id: str):
        """Return a ``(folder, title) -> note_id | None`` resolver for rendering wikilinks."""

        def resolve(folder: str, title: str) -> str | None:
            note = self._repo.get_by_path(ws_name, owner_id, folder, title)
            return note.id if note else None

        return resolve

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

    def preview_chunks(self, note_id: str, owner_id: str, ws_path: str) -> dict | None:
        """Live chunk preview for a note (reads current file content; never stored rows)."""
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        data = self.get_with_content(note_id, owner_id, ws_path)
        if data is None:
            return None
        chunks = (
            self._indexer.preview(note.title, data["content"], owner_id) if self._indexer else []
        )
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
        tags: builtins.list[str] | None = None,
        folder: str | None = None,
        mode: str = "overwrite",
        target_heading: str | None = None,
        old_text: str | None = None,
        confirm: bool = False,
    ) -> dict:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        now = datetime.now(UTC).isoformat()
        new_title = title if title is not None else note.title
        try:
            new_folder = normalize_folder(folder) if folder is not None else note.folder
        except ValueError as e:
            raise InvalidFolderError(str(e)) from e
        current_tags = json.loads(note.tags or "[]")
        new_tags = self._normalize_tags(tags) if tags is not None else current_tags

        old_path = note_filepath(ws_path, note.folder, note.title)
        new_path = note_filepath(ws_path, new_folder, new_title)
        old_rel = str(Path(old_path).relative_to(ws_path))
        new_rel = str(Path(new_path).relative_to(ws_path))

        if not Path(old_path).exists():
            raise FileNotFoundError(f"Plik notatki {note_id} nie znaleziony.")
        if old_path != new_path:
            if not self._repo.check_unique(note.workspace, owner_id, new_folder, new_title):
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
            if content is None:
                raise ValueError("content jest wymagany dla trybu edycji.")
            new_content = apply_edit(old_content, mode, content, target_heading, old_text)

        # Validate links on the final content (post apply_edit), before any git mutation.
        target_ids = self._validate_wikilinks(note.workspace, owner_id, new_content)

        old_fm_tags = self._normalize_tags(note_data["tags"])
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
            return self._confirmation_payload(note_id, would_remove, overwrites_content)

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
            self._repo.update(
                note_id,
                owner_id=owner_id,
                title=new_title,
                content=new_content,
                tags=new_tags,
                updated_at=now,
                folder=new_folder,
            )
            self._repo.replace_links(note_id, note.workspace, owner_id, target_ids)
            self._sync_tags(note_id, note.workspace, owner_id, new_tags, new_content)
        if old_path != new_path:
            self._rewrite_backlinks(
                note_id, owner_id, ws_path, note.folder, note.title, new_folder, new_title
            )
        if self._cache is not None:
            self._cache.bump(note.workspace, owner_id)
        logger.info("note_updated", note_id=note_id, folder=new_folder)
        self._index(note_id, note.workspace, owner_id, new_title, new_content)
        return {"note_id": note_id}

    def move(self, note_id: str, owner_id: str, ws_path: str, folder: str) -> dict:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")

        try:
            new_folder = normalize_folder(folder)
        except ValueError as e:
            raise InvalidFolderError(str(e)) from e
        if new_folder == note.folder:
            return {"note_id": note_id, "folder": new_folder}

        old_path = Path(note_filepath(ws_path, note.folder, note.title))
        new_path = Path(note_filepath(ws_path, new_folder, note.title))
        if not old_path.exists():
            raise FileNotFoundError(f"Plik notatki {note_id} nie znaleziony.")
        if not self._repo.check_unique(note.workspace, owner_id, new_folder, note.title):
            raise FileExistsError(
                f"Notatka '{note.title}' już istnieje w folderze '{new_folder or 'root'}'."
            )
        if new_path.exists():
            raise FileExistsError(f"Plik docelowy '{new_path.relative_to(ws_path)}' już istnieje.")

        old_rel = str(old_path.relative_to(ws_path))
        new_rel = str(new_path.relative_to(ws_path))
        new_path.parent.mkdir(parents=True, exist_ok=True)
        GitRepository(ws_path).rename_file(
            old_rel, new_rel, f"note: move {note.title} to {new_folder or 'root'}"
        )
        self._repo.update(
            note_id,
            owner_id=owner_id,
            updated_at=note.updated_at,
            folder=new_folder,
        )
        self._rewrite_backlinks(
            note_id, owner_id, ws_path, note.folder, note.title, new_folder, note.title
        )
        prune_empty_parents(ws_path, note.folder)
        if self._cache is not None:
            self._cache.bump(note.workspace, owner_id)
        logger.info("note_moved", note_id=note_id, folder=new_folder)
        return {"note_id": note_id, "folder": new_folder}

    def move_folder(
        self, src: str, dst: str, *, owner_id: str, ws_path: str, workspace: str
    ) -> dict:
        """Move/rename a folder and all notes under it; merges when ``dst`` exists.

        Aborts atomically (nothing moved) if any note would collide with an existing
        note in the destination. Chunks are untouched (folder is note metadata, not part
        of a chunk), so no re-embedding happens."""
        try:
            src_n = normalize_folder(src)
            dst_n = normalize_folder(dst)
        except ValueError as e:
            raise InvalidFolderError(str(e)) from e
        if not src_n:
            raise InvalidFolderError("Nie można przenieść folderu root.")
        if dst_n == src_n:
            return {"moved": 0, "src": src_n, "dst": dst_n}
        if dst_n.startswith(src_n + "/"):
            raise InvalidFolderError("Nie można przenieść folderu do jego podkatalogu.")

        notes = self._repo.list_under_folder(workspace, owner_id, src_n)
        src_root = Path(ws_path, *src_n.split("/"))
        if not notes and not src_root.exists():
            raise FileNotFoundError(f"Folder '{src_n}' nie istnieje.")

        remap: dict[str, str] = {}
        conflicts: list[dict] = []
        for note in notes:
            remainder = note.folder[len(src_n) :].lstrip("/")
            new_folder = "/".join(p for p in (dst_n, remainder) if p)
            remap[note.id] = new_folder
            if not self._repo.check_unique(workspace, owner_id, new_folder, note.title):
                conflicts.append({"title": note.title, "folder": new_folder})
        if conflicts:
            return {"error": "Notatki o tych nazwach już istnieją w celu.", "conflicts": conflicts}

        files = [p for p in src_root.rglob("*") if p.is_file()] if src_root.exists() else []
        rels_under_src = [p.relative_to(src_root) for p in files]
        removed_rels = [str(p.relative_to(ws_path)) for p in files]

        # Note file (relative to src_root) -> its destination path (relative to ws).
        note_targets: dict[str, str] = {}
        for note in notes:
            under_src = Path(note_filepath(ws_path, note.folder, note.title)).relative_to(src_root)
            new_rel = Path(note_filepath(ws_path, remap[note.id], note.title)).relative_to(ws_path)
            note_targets[str(under_src)] = str(new_rel)

        # Move through a temp dir: makes case-only renames work on case-insensitive
        # filesystems and keeps the source from self-colliding with the destination.
        tmp_root = Path(ws_path, f".kajet-move-{token_hex(8)}")
        added_rels: list[str] = []
        done: list[tuple[Path, Path]] = []
        if src_root.exists():
            src_root.rename(tmp_root)
            try:
                for rel in rels_under_src:
                    key = str(rel)
                    if key in note_targets:
                        new_rel = note_targets[key]
                    else:
                        # Aux file (e.g. .gitkeep): same sub-position under dst, unless the
                        # destination already has it (merge) — then drop it with the temp dir.
                        dst_base = Path(ws_path, *dst_n.split("/")) if dst_n else Path(ws_path)
                        target = dst_base / rel
                        if target.exists():
                            continue
                        new_rel = str(target.relative_to(ws_path))
                    dest = Path(ws_path, new_rel)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    (tmp_root / rel).rename(dest)
                    done.append((tmp_root / rel, dest))
                    added_rels.append(new_rel)
            except Exception:
                for src_file, dest in reversed(done):
                    src_file.parent.mkdir(parents=True, exist_ok=True)
                    dest.rename(src_file)
                tmp_root.rename(src_root)
                raise
            shutil.rmtree(tmp_root, ignore_errors=True)

        GitRepository(ws_path).commit_moves(
            removed_rels, added_rels, f"folder: move {src_n} -> {dst_n or 'root'}"
        )
        # Update every folder column first, THEN rewrite backlinks: a link from one
        # moved note to another (same folder being moved) is only found if the source
        # note's DB folder already points at its new — and now real — file location.
        for note in notes:
            self._repo.update(
                note.id, owner_id=owner_id, updated_at=note.updated_at, folder=remap[note.id]
            )
        for note in notes:
            self._rewrite_backlinks(
                note.id, owner_id, ws_path, note.folder, note.title, remap[note.id], note.title
            )
        remove_empty_tree(ws_path, src_n)
        if self._cache is not None:
            self._cache.bump(workspace, owner_id)
        logger.info("folder_moved", src=src_n, dst=dst_n, count=len(notes))
        return {"moved": len(notes), "src": src_n, "dst": dst_n}

    def prune_empty_folders(self, ws_path: str) -> dict:
        """Remove every completely-empty directory (orphans left by past moves). Folders
        holding a ``.gitkeep`` are kept."""
        removed = prune_all_empty_dirs(ws_path)
        return {"pruned": removed, "count": len(removed)}

    def _apply_tag_change(
        self,
        note_id: str,
        owner_id: str,
        ws_path: str,
        mutate: Callable[[builtins.list[str], str], tuple[builtins.list[str], builtins.list[str]]],
    ) -> dict:
        """Read the note's frontmatter tags, apply ``mutate`` -> (new_tags, warnings),
        and persist only if the list changed. Returns the effective state.

        The file (not the DB column) is the source of truth for the current list, so the
        change is computed against on-disk reality. Content/title are never touched.
        """
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        if not Path(filepath).exists():
            raise FileNotFoundError(f"Plik notatki {note_id} nie znaleziony.")
        data = read_note_file(filepath)
        content = data["content"]
        current = self._normalize_tags(data["tags"])
        new_tags, warnings = mutate(current, content)
        if new_tags != current:
            now = datetime.now(UTC).isoformat()
            relative = str(Path(filepath).relative_to(ws_path))
            repo = GitRepository(ws_path)
            try:
                write_note_file(
                    filepath, note_id, note.title, new_tags, note.created_at, now, content
                )
                repo.commit_file(relative, f"note: tag {note.title}")
            except GitError:
                write_note_file(
                    filepath,
                    note_id,
                    note.title,
                    current,
                    note.created_at,
                    note.updated_at,
                    content,
                )
                raise
            self._repo.update(
                note_id,
                owner_id=owner_id,
                title=note.title,
                content=content,
                tags=new_tags,
                updated_at=now,
                folder=note.folder,
            )
            self._sync_tags(note_id, note.workspace, owner_id, new_tags, content)
            if self._cache is not None:
                self._cache.bump(note.workspace, owner_id)
            logger.info("note_tags_changed", note_id=note_id)
        inline = extract_inline_tags(content)
        effective = list(dict.fromkeys([*new_tags, *sorted(inline)]))
        return {
            "note_id": note_id,
            "tags": effective,
            "frontmatter_tags": new_tags,
            "warnings": warnings,
        }

    def add_tags(self, note_id: str, owner_id: str, ws_path: str, tags: builtins.list[str]) -> dict:
        """Union ``tags`` into the note's frontmatter list (idempotent, order-preserving)."""

        def mutate(
            current: builtins.list[str], content: str
        ) -> tuple[builtins.list[str], builtins.list[str]]:
            normalized, warnings = self._normalize_with_warnings(tags)
            new_tags = list(dict.fromkeys([*current, *normalized]))
            return new_tags, warnings

        return self._apply_tag_change(note_id, owner_id, ws_path, mutate)

    def remove_tags(
        self, note_id: str, owner_id: str, ws_path: str, tags: builtins.list[str]
    ) -> dict:
        """Remove ``tags`` from the note's frontmatter list (idempotent).

        A requested tag that exists only as an inline ``#hashtag`` in the body cannot be
        removed here (that would mean editing prose); it is reported as a warning instead.
        """

        def mutate(
            current: builtins.list[str], content: str
        ) -> tuple[builtins.list[str], builtins.list[str]]:
            normalized, warnings = self._normalize_with_warnings(tags)
            to_remove = set(normalized)
            new_tags = [t for t in current if t not in to_remove]
            inline = extract_inline_tags(content)
            for tag in normalized:
                if tag in inline:
                    warnings.append(
                        f"{tag}: nadal obecny jako #{tag} w treści — "
                        "usuń edytując body przez edit_note"
                    )
            return new_tags, warnings

        return self._apply_tag_change(note_id, owner_id, ws_path, mutate)

    @staticmethod
    def _confirmation_payload(
        note_id: str,
        would_remove: builtins.list[str],
        overwrites_content: bool,
    ) -> dict:
        """Non-applied result telling the caller a destructive op needs confirmation."""
        parts: builtins.list[str] = []
        if would_remove:
            parts.append(f"usunie tagi: {', '.join(would_remove)}")
        if overwrites_content:
            parts.append("nadpisze istniejącą treść notatki")
        return {
            "note_id": note_id,
            "requires_confirmation": True,
            "would_remove_tags": would_remove,
            "overwrites_content": overwrites_content,
            "warning": (
                "Operacja destrukcyjna: "
                + "; ".join(parts)
                + ". Potwierdź z użytkownikiem i zawołaj ponownie z confirm=true."
            ),
        }

    def set_tags(
        self,
        note_id: str,
        owner_id: str,
        ws_path: str,
        tags: builtins.list[str],
        confirm: bool = False,
    ) -> dict:
        """Overwrite the note's frontmatter list with ``tags`` (tag-only alias of update).

        Destructive: if it would drop existing frontmatter tags and ``confirm`` is False,
        nothing is written and a ``requires_confirmation`` payload is returned instead.
        """
        normalized, warnings = self._normalize_with_warnings(tags)
        new_set = set(normalized)

        def mutate(
            current: builtins.list[str], content: str
        ) -> tuple[builtins.list[str], builtins.list[str]]:
            would_remove = [t for t in current if t not in new_set]
            if would_remove and not confirm:
                raise _ConfirmationRequired(
                    self._confirmation_payload(note_id, would_remove, False)
                )
            return normalized, warnings

        try:
            return self._apply_tag_change(note_id, owner_id, ws_path, mutate)
        except _ConfirmationRequired as exc:
            return exc.payload

    def delete(self, note_id: str, owner_id: str, ws_path: str) -> None:
        note = self._repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        if Path(filepath).exists():
            relative = str(Path(filepath).relative_to(ws_path))
            GitRepository(ws_path).delete_file(relative, f"note: delete {note_id}")
        self._repo.delete_note_tags(note_id, note.workspace, owner_id)
        self._clear_index(note_id)
        self._repo.delete(note_id, owner_id=owner_id)
        self._repo.delete_links_from(note_id)
        self._repo.delete_links_to(note_id)
        if self._cache is not None:
            self._cache.bump(note.workspace, owner_id)
        logger.info("note_deleted", note_id=note_id)

    def list(
        self,
        ws_name: str,
        owner_id: str,
        tags: builtins.list[str] | None = None,
        limit: int | None = 20,
        folder: str | None = None,
        include_descendants: bool = True,
    ) -> builtins.list[dict]:
        return self._repo.list(
            ws_name,
            owner_id=owner_id,
            tags=tags,
            limit=limit,
            folder=folder,
            include_descendants=include_descendants,
        )

    def list_folders(self, ws_path: str) -> builtins.list[str]:
        return list_workspace_folders(ws_path)

    def tag_tree(self, ws_name: str, owner_id: str) -> builtins.list[dict]:
        return self._repo.tag_tree(ws_name, owner_id)

    def notes_by_tag(
        self,
        ws_name: str,
        owner_id: str,
        path: str,
        include_descendants: bool = True,
        limit: int | None = None,
    ) -> builtins.list[dict]:
        return self._repo.notes_by_tag(ws_name, owner_id, path, include_descendants, limit)

    def search(
        self,
        query: str,
        workspaces: builtins.list[str],
        owner_id: str,
        limit: int = 10,
    ) -> builtins.list[dict]:
        # Resolve the backend identity up front so it is part of the cache key: a config
        # change (backend switch / key add) must not keep serving the old backend's ranking
        # from cache. resolve is a cheap indexed read, fine to run on cache hits too.
        cfg = None
        if self._query_resolver is not None:
            try:
                cfg = self._query_resolver(owner_id)
            except Exception:
                cfg = None
        # An active profile (even keyless — a local/no-auth endpoint) drives vector search.
        embeddable = cfg is not None
        backend_key = (cfg.backend_id, cfg.dim) if embeddable else None

        key = None
        if self._cache is not None:
            epochs = tuple(self._cache.epoch(ws, owner_id) for ws in workspaces)
            key = ("search", owner_id, tuple(workspaces), epochs, query, limit, backend_key)
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        embedding = None
        dim = None
        if embeddable:
            try:
                vec = self._embed_query(cfg, query)
                embedding = pack_vector(vec)
                dim = cfg.dim
            except Exception:
                logger.warning("search_embed_failed", backend=cfg.backend_id)
        per_ws_limit = limit * 3 if len(workspaces) > 1 else limit
        results = []
        for ws in workspaces:
            hits = self._repo.hybrid_search(
                query, ws, owner_id, embedding=embedding, dim=dim, limit=per_ws_limit
            )
            results.extend(hits)
        results = results[:limit]
        if self._cache is not None and key is not None:
            self._cache.put(key, results)
        logger.info(
            "search_performed", query_len=len(query), results=len(results), ws_count=len(workspaces)
        )
        return results

    def _embed_query(self, cfg, query: str) -> builtins.list[float]:
        if self._query_cache is not None:
            cached = self._query_cache.get(query, cfg.backend_id, cfg.model)
            if cached is not None:
                return cached
        # Only reached when search() resolved a backend, which is wired together with
        # build_embedder in the DI container; the None default is for cache-only test doubles.
        embedder = self._build_embedder(cfg)  # ty: ignore[call-non-callable] - optional DI seam
        vec = asyncio.run(embedder.embed_query(query))
        if self._query_cache is not None:
            self._query_cache.put(query, cfg.backend_id, cfg.model, vec)
        return vec

    def reindex(self, ws_name: str, owner_id: str, ws_path: str) -> dict:
        start = time.monotonic()
        notes = scan_notes(ws_path)
        self._repo.delete_workspace_tags(ws_name, owner_id)
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
        # Second pass: rebuild the link graph now that every note is resolvable.
        # Broken links are silently skipped here (reindex must not fail on pre-existing data).
        self._repo.delete_workspace_links(ws_name, owner_id)
        for note in notes:
            if not note["id"]:
                continue
            fm_tags = self._normalize_tags(note["tags"] or [])
            self._sync_tags(note["id"], ws_name, owner_id, fm_tags, note["content"] or "")
            pairs = [split_target(target) for target, _ in extract_wikilinks(note["content"] or "")]
            if not pairs:
                continue
            resolved = self._repo.resolve_paths(ws_name, owner_id, pairs)
            if resolved:
                self._repo.replace_links(note["id"], ws_name, owner_id, set(resolved.values()))
        if self._indexer is not None:
            try:
                index_payload = [
                    {"id": n["id"], "title": n["title"] or "", "content": n["content"] or ""}
                    for n in notes
                    if n["id"]
                ]
                self._indexer.index_many(ws_name, owner_id, index_payload)
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

    def get_history(
        self, note_id: str, owner_id: str, ws_path: str, limit: int = 50
    ) -> builtins.list[dict]:
        note = self._repo.get(note_id, owner_id=owner_id)
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
        note = self._repo.get(note_id, owner_id=owner_id)
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

    def restore_version(self, note_id: str, sha: str, owner_id: str, ws_path: str) -> dict:
        version = self.get_version(note_id, sha, owner_id, ws_path)
        # Version restore is an explicit intent — always bypass the destructive-op gate.
        return self.update(
            note_id, owner_id=owner_id, ws_path=ws_path, content=version["content"], confirm=True
        )
