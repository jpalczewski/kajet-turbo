import shutil
from pathlib import Path
from secrets import token_hex

from sqlmodel import Session

from kajet_turbo.log import logger
from kajet_turbo.markdown import IndexedNote
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.repositories.git import (
    GitError,
    GitRepository,
    target_write_transaction,
    workspace_write_transaction,
)
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.notes.links import NoteLinkService
from kajet_turbo.services.notes.paths import (
    build_path_index,
    conflict_message,
    note_path_conflict,
    path_conflict_key,
)
from kajet_turbo.services.notes.staged_change import (
    StagedChange,
    commit_rows_then_tree,
)
from kajet_turbo.services.targets import NoteTarget
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

# Above this many notes, move_folder refuses before touching disk: an oversized folder
# move is expensive and irreversible once the temp-dir choreography starts, and — unlike
# rename_tag/_rewrite_backlinks (#171) — the caller has a real workaround (move a
# subfolder at a time).
_MOVE_FOLDER_MAX_NOTES = 5000


class NoteFolderService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        link_service: NoteLinkService,
        folder_meta_repo: FolderMetaRepository | None = None,
        reconcile_repo: LinkReconcileRepository | None = None,
    ):
        self._crud_repo = crud_repo
        self._link_service = link_service
        self._folder_meta_repo = folder_meta_repo
        self._reconcile_repo = reconcile_repo

    @target_write_transaction
    def move(self, target: NoteTarget, folder: str) -> dict:
        note_id = target.note_id
        owner_id = target.workspace.owner_id
        ws_path = str(target.workspace.path)
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Note not found: note_id={note_id}")

        try:
            new_folder = normalize_folder(folder)
        except ValueError as e:
            raise InvalidFolderError(str(e)) from e
        if new_folder == note.folder:
            return {"note_id": note_id, "folder": new_folder}

        old_path = Path(note_filepath(ws_path, note.folder, note.title))
        new_path = Path(note_filepath(ws_path, new_folder, note.title))
        if not old_path.exists():
            raise FileNotFoundError(f"Note file not found: note_id={note_id}")

        workspace_links = self._link_service.for_workspace(note.workspace, owner_id)
        conflict = note_path_conflict(
            workspace_links.paths, ws_path, new_folder, note.title, exclude_id=note_id
        )
        if conflict is not None:
            raise FileExistsError(conflict_message(note.title, str(new_path), conflict))

        affected_sources = workspace_links.affected_sources(
            {note.title}, include_source_ids={note_id}
        )
        old_rel = str(old_path.relative_to(ws_path))
        new_rel = str(new_path.relative_to(ws_path))
        repo = GitRepository(ws_path)

        def apply_move() -> None:
            # Route through a temp name in old_path's own folder — same choreography
            # as move_folder's tmp_root (#181) — so a case-only folder rename never
            # self-collides against its own not-yet-moved source, and the exists()
            # check below is meaningful regardless of the filesystem's case
            # sensitivity, instead of a pre-check that would misfire against the
            # source's own alias on a case-insensitive-but-case-preserving filesystem
            # (macOS APFS, Windows NTFS).
            tmp_path = old_path.parent / f".kajet-move-{token_hex(8)}"
            try:
                old_path.rename(tmp_path)
            except OSError as e:
                raise GitError(str(e)) from e
            if new_path.exists():
                tmp_path.rename(old_path)
                raise FileExistsError(f"Target file '{new_rel}' already exists.")
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path.rename(new_path)
            except OSError as e:
                tmp_path.rename(old_path)
                raise GitError(str(e)) from e

        item = StagedChange(add=new_rel, remove=old_rel, apply=apply_move)
        message = f"note: move {note.title} to {new_folder or 'root'}"

        def write_rows(session: Session) -> None:
            self._crud_repo.update_in_session(
                session,
                note_id,
                owner_id=owner_id,
                updated_at=note.updated_at,
                folder=new_folder,
            )

        commit_rows_then_tree(
            self._crud_repo,
            repo,
            [item],
            message,
            operation="move",
            write_rows=write_rows,
            note_id=note_id,
            owner_id=owner_id,
            folder=new_folder,
        )
        move = (
            IndexedNote(note_id, note.folder, note.title),
            IndexedNote(note_id, new_folder, note.title),
        )
        workspace_links.rewrite_backlinks([move], ws_path, repo)
        prune_empty_parents(ws_path, note.folder)
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
        if len(notes) > _MOVE_FOLDER_MAX_NOTES:
            raise ValueError(
                f"Moving '{src_n}' would touch {len(notes)} notes, above the "
                f"{_MOVE_FOLDER_MAX_NOTES} safety threshold. Move a subfolder at a time instead."
            )

        workspace_links = self._link_service.for_workspace(workspace, owner_id)
        # Every OTHER note's current path, indexed once for an O(1) lookup per note below
        # instead of an O(len(notes in workspace)) rescan. Notes actually being moved are
        # excluded up front: with a case-insensitive comparison key, a case-only folder
        # rename (move_folder("projekty", "Projekty")) would otherwise have every moved
        # note's new folded path equal its own old folded path, and find itself as a false
        # "conflict". A separate `claimed` dict below catches new-target collisions BETWEEN
        # two notes moved together in this same call, which this static, pre-move index
        # cannot see (their entries here still carry their old folders).
        moved_ids = {note.id for note in notes}
        path_index = build_path_index(
            (p for p in workspace_links.paths if p.note_id not in moved_ids), ws_path
        )
        remap: dict[str, str] = {}
        conflicts: list[dict] = []
        claimed: dict[str, str] = {}
        for note in notes:
            remainder = note.folder[len(src_n) :].lstrip("/")
            new_folder = "/".join(p for p in (dst_n, remainder) if p)
            remap[note.id] = new_folder
            target = note_filepath(ws_path, new_folder, note.title)
            target_rel = str(Path(target).relative_to(ws_path))
            target_key = path_conflict_key(target)
            if path_index.get(target_key) is not None or target_key in claimed:
                conflicts.append({"title": note.title, "folder": new_folder})
            else:
                claimed[target_key] = target_rel
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

        repo = GitRepository(ws_path)
        message = f"folder: move {src_n} -> {dst_n or 'root'}"

        # Unlike every other write path in this package, move_folder commits the git tree
        # *first*, unconditionally — the temp-dir choreography above already physically
        # moved every file, so there is no "nothing touches disk until the DB write
        # succeeds" guarantee left to buy by deferring this. Committing git first restores
        # the pre-#155 property that a DB-write failure only ever leaves rows *behind* an
        # already-committed tree — the direction reconcile_paths heals — never the reverse.
        # See services/notes/CLAUDE.md's #155 section.
        repo.commit_changes(removed=removed_rels, added=added_rels, message=message)

        # Update every folder column in one transaction — safe to leave unchunked because
        # _MOVE_FOLDER_MAX_NOTES above already bounds it to a few thousand plain UPDATEs,
        # and (unlike the old commit_rows_then call) no git commit runs inside this
        # transaction to make its hold time a concern. Chunking here would trade this
        # atomicity for no benefit: a failure partway through a chunked write would leave
        # the git tree fully reflecting the move while only some chunks' rows updated, and
        # skip the backlink rewrite/cleanup below entirely for every note including
        # already-committed chunks — worse than the single-transaction shape kept here.
        # THEN rewrite backlinks: a link from one moved note to another (same folder being
        # moved) is only found if the source note's DB folder already points at its new —
        # and now real — file location.
        if notes:
            with (
                self._crud_repo.operation(
                    "move_folder",
                    workspace=workspace,
                    owner_id=owner_id,
                    src=src_n,
                    dst=dst_n,
                    count=len(notes),
                ) as op,
                op.session.begin(),
            ):
                for note in notes:
                    self._crud_repo.update_in_session(
                        op.session,
                        note.id,
                        owner_id=owner_id,
                        updated_at=note.updated_at,
                        folder=remap[note.id],
                    )
        moves = [
            (
                IndexedNote(note.id, note.folder, note.title),
                IndexedNote(note.id, remap[note.id], note.title),
            )
            for note in notes
        ]
        try:
            workspace_links.rewrite_backlinks(moves, ws_path, repo)
        finally:
            # rewrite_backlinks chunks internally (#171): a failure partway through can
            # leave some of affected_sources rewritten and some not, with the exception
            # propagating before this method would otherwise reach mark_and_enqueue below.
            # Marking regardless of success queues the lazy ReconcileLinksHandler pass
            # (services/notes/CLAUDE.md's "Link resolution has two consistency tiers") as a
            # safety net for whatever a partial rewrite left stale — the move itself already
            # committed by this point either way, so there's nothing to roll back here.
            if self._reconcile_repo is not None:
                self._reconcile_repo.mark_and_enqueue(owner_id, workspace, affected_sources)
        remove_empty_tree(ws_path, src_n)
        if self._folder_meta_repo is not None:
            self._folder_meta_repo.rename_paths(owner_id, workspace, src_n, dst_n)
        logger.info("folder_moved", src=src_n, dst=dst_n, count=len(notes))
        return {"moved": len(notes), "src": src_n, "dst": dst_n}

    @workspace_write_transaction
    def prune_empty_folders(self, ws_path: str) -> dict:
        """Remove every completely-empty directory (orphans left by past moves). Folders
        holding a ``.gitkeep`` are kept."""
        removed = prune_all_empty_dirs(ws_path)
        return {"pruned": removed, "count": len(removed)}

    def list_folders(self, ws_path: str) -> list[str]:
        return list_workspace_folders(ws_path)
