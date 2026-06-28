import shutil
from pathlib import Path
from secrets import token_hex

from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.log import logger
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.notes.links import NoteLinkService
from kajet_turbo.workspace import (
    InvalidFolderError,
    list_workspace_folders,
    normalize_folder,
    note_filepath,
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
    ):
        self._crud_repo = crud_repo
        self._link_service = link_service
        self._cache = cache

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
        if not self._crud_repo.check_unique(note.workspace, owner_id, new_folder, note.title):
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
        self._crud_repo.update(
            note_id,
            owner_id=owner_id,
            updated_at=note.updated_at,
            folder=new_folder,
        )
        self._link_service.rewrite_backlinks(
            note_id,
            owner_id,
            ws_path,
            note.workspace,
            note.folder,
            note.title,
            new_folder,
            note.title,
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

        notes = self._crud_repo.list_under_folder(workspace, owner_id, src_n)
        src_root = Path(ws_path, *src_n.split("/"))
        if not notes and not src_root.exists():
            raise FileNotFoundError(f"Folder '{src_n}' nie istnieje.")

        remap: dict[str, str] = {}
        conflicts: list[dict] = []
        for note in notes:
            remainder = note.folder[len(src_n) :].lstrip("/")
            new_folder = "/".join(p for p in (dst_n, remainder) if p)
            remap[note.id] = new_folder
            if not self._crud_repo.check_unique(workspace, owner_id, new_folder, note.title):
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
            self._crud_repo.update(
                note.id, owner_id=owner_id, updated_at=note.updated_at, folder=remap[note.id]
            )
        for note in notes:
            self._link_service.rewrite_backlinks(
                note.id,
                owner_id,
                ws_path,
                workspace,
                note.folder,
                note.title,
                remap[note.id],
                note.title,
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

    def list_folders(self, ws_path: str) -> list[str]:
        return list_workspace_folders(ws_path)
