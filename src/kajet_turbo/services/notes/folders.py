import shutil
from pathlib import Path
from secrets import token_hex

from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.log import logger
from kajet_turbo.markdown import IndexedNote
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.repositories.git import GitRepository, workspace_write_transaction
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.notes.links import NoteLinkService
from kajet_turbo.services.notes.paths import build_path_index, conflict_message, note_path_conflict
from kajet_turbo.workspace import (
    InvalidFolderError,
    list_workspace_folders,
    normalize_folder,
    note_filepath,
    path_segments,
    prune_all_empty_dirs,
    prune_empty_parents,
    remove_empty_tree,
)


class NoteFolderService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        link_service: NoteLinkService,
        cache: WorkspaceCache | None,
        folder_meta_repo: FolderMetaRepository | None = None,
        reconcile_repo: LinkReconcileRepository | None = None,
    ):
        self._crud_repo = crud_repo
        self._link_service = link_service
        self._cache = cache
        self._folder_meta_repo = folder_meta_repo
        self._reconcile_repo = reconcile_repo

    @workspace_write_transaction
    def move(self, note_id: str, owner_id: str, ws_path: str, folder: str) -> dict:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
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

        workspace_links = self._link_service.for_workspace(note.workspace, owner_id)
        conflict = note_path_conflict(
            workspace_links.paths, ws_path, new_folder, note.title, exclude_id=note_id
        )
        if conflict is not None:
            raise FileExistsError(conflict_message(note.title, str(new_path), conflict))
        if new_path.exists():
            raise FileExistsError(f"Target file '{new_path.relative_to(ws_path)}' already exists.")

        affected_sources = workspace_links.affected_sources(
            {note.title}, include_source_ids={note_id}
        )
        old_rel = str(old_path.relative_to(ws_path))
        new_rel = str(new_path.relative_to(ws_path))
        GitRepository(ws_path).rename_file(
            old_rel, new_rel, f"note: move {note.title} to {new_folder or 'root'}"
        )
        self._crud_repo.update(
            note_id,
            owner_id=owner_id,
            updated_at=note.updated_at,
            folder=new_folder,
        )
        move = (
            IndexedNote(note_id, note.folder, note.title),
            IndexedNote(note_id, new_folder, note.title),
        )
        workspace_links.rewrite_backlinks([move], ws_path)
        prune_empty_parents(ws_path, note.folder)
        if self._cache is not None:
            self._cache.bump(note.workspace, owner_id)
        logger.info("note_moved", note_id=note_id, folder=new_folder)
        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(owner_id, note.workspace, affected_sources)
        return {"note_id": note_id, "folder": new_folder}

    @workspace_write_transaction
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

        notes = self._crud_repo.list_under_folder(workspace, owner_id, src_n)
        src_root = Path(ws_path, *path_segments(src_n))
        if not notes and not src_root.exists():
            raise FileNotFoundError(f"Folder '{src_n}' nie istnieje.")

        workspace_links = self._link_service.for_workspace(workspace, owner_id)
        # Every note's *current* path, indexed once for an O(1) lookup per note below
        # instead of an O(len(notes in workspace)) rescan — this only ever matches a
        # note NOT in this move (a target can't collide with its own old path: dst_n !=
        # src_n is already guaranteed above, so a moved note's old and new folders always
        # differ). A separate `claimed` dict below catches new-target collisions BETWEEN
        # two notes moved together in this same call, which this static, pre-move index
        # cannot see (their entries here still carry their old folders).
        path_index = build_path_index(workspace_links.paths, ws_path)
        remap: dict[str, str] = {}
        conflicts: list[dict] = []
        claimed: dict[str, str] = {}
        for note in notes:
            remainder = note.folder[len(src_n) :].lstrip("/")
            new_folder = "/".join(p for p in (dst_n, remainder) if p)
            remap[note.id] = new_folder
            target = note_filepath(ws_path, new_folder, note.title)
            target_rel = str(Path(target).relative_to(ws_path))
            if path_index.get(target) is not None or target_rel in claimed:
                conflicts.append({"title": note.title, "folder": new_folder})
            else:
                claimed[target_rel] = note.title
        if conflicts:
            return {
                "error": "Notes with these names already exist at the destination.",
                "conflicts": conflicts,
            }

        affected_sources = workspace_links.affected_sources(
            {note.title for note in notes},
            include_source_ids={note.id for note in notes},
        )
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
                        dest = Path(ws_path, new_rel)
                        # The pre-flight loop above only checks DB rows, not disk (a
                        # case-only rename's own destination would falsely "exist" before
                        # src_root is relocated to tmp_root — see the comment above). Now
                        # that the source is safely out of the way, a file still sitting
                        # at dest has no matching note record: reject rather than silently
                        # overwrite it, same as every other write site in this module.
                        if dest.exists():
                            raise FileExistsError(
                                f"Target file '{new_rel}' already exists on disk "
                                "(no matching note record)."
                            )
                    else:
                        # Aux file (e.g. .gitkeep): same sub-position under dst, unless the
                        # destination already has it (merge) — then drop it with the temp dir.
                        target = Path(ws_path, *path_segments(dst_n), rel)
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
            self._crud_repo.update(
                note.id, owner_id=owner_id, updated_at=note.updated_at, folder=remap[note.id]
            )
        moves = [
            (
                IndexedNote(note.id, note.folder, note.title),
                IndexedNote(note.id, remap[note.id], note.title),
            )
            for note in notes
        ]
        workspace_links.rewrite_backlinks(moves, ws_path)
        remove_empty_tree(ws_path, src_n)
        if self._folder_meta_repo is not None:
            self._folder_meta_repo.rename_paths(owner_id, workspace, src_n, dst_n)
        if self._cache is not None:
            self._cache.bump(workspace, owner_id)
        logger.info("folder_moved", src=src_n, dst=dst_n, count=len(notes))
        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(owner_id, workspace, affected_sources)
        return {"moved": len(notes), "src": src_n, "dst": dst_n}

    @workspace_write_transaction
    def prune_empty_folders(self, ws_path: str) -> dict:
        """Remove every completely-empty directory (orphans left by past moves). Folders
        holding a ``.gitkeep`` are kept."""
        removed = prune_all_empty_dirs(ws_path)
        return {"pruned": removed, "count": len(removed)}

    def list_folders(self, ws_path: str) -> list[str]:
        return list_workspace_folders(ws_path)
