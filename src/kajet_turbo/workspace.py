import os
import re
from pathlib import Path

import frontmatter

from kajet_turbo.repositories.git import GitRepository

WORKSPACES_DIR = os.getenv("WORKSPACES_DIR", "/workspaces")

_WINDOWS_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WINDOWS_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE)


class InvalidFolderError(ValueError):
    pass


def title_to_windows_filename(title: str) -> str:
    result = _WINDOWS_FORBIDDEN.sub(" ", title)
    result = re.sub(r" +", " ", result)
    result = result.strip().rstrip(". ")
    if _WINDOWS_RESERVED.match(result):
        result = "_" + result
    if not result:
        result = "untitled"
    return result[:200]


def normalize_folder(folder: str) -> str:
    folder = folder.strip().strip("/")
    if not folder:
        return ""
    parts = [s for s in folder.split("/") if s]
    for part in parts:
        if part == "..":
            raise ValueError("Invalid folder: '..' not allowed")
    return "/".join(title_to_windows_filename(p) for p in parts)


def list_workspace_folders(workspace_path: str) -> list[str]:
    """List visible workspace folders from disk. Root is represented by an empty string."""
    root = Path(workspace_path).resolve()
    if not root.is_dir():
        return [""]
    folders = [""]
    for path in root.rglob("*"):
        if not path.is_dir():
            continue
        parts = path.relative_to(root).parts
        if any(part.startswith(".") for part in parts):
            continue
        folders.append("/".join(parts))
    return sorted(folders)


def _is_empty_dir(path: Path) -> bool:
    """A directory with no entries at all. A folder holding only ``.gitkeep`` is
    NOT empty — that file marks an intentionally-kept empty folder."""
    return path.is_dir() and not any(path.iterdir())


def prune_empty_parents(ws_path: str, folder: str) -> list[str]:
    """Remove now-empty ancestor dirs of ``folder`` bottom-up, stopping at the first
    non-empty dir (which includes any folder containing ``.gitkeep``) or the root.
    Git does not track directories, so this is a pure filesystem op. Returns the
    folder paths removed."""
    root = Path(ws_path).resolve()
    removed: list[str] = []
    current = folder
    while current:
        target = root / current
        if not _is_empty_dir(target):
            break
        target.rmdir()
        removed.append(current)
        current = str(Path(current).parent) if "/" in current else ""
    return removed


def prune_all_empty_dirs(ws_path: str) -> list[str]:
    """Remove every completely-empty directory in the workspace, bottom-up so
    emptied parents cascade. Skips hidden dirs (e.g. ``.git``) and keeps folders
    holding a ``.gitkeep``. Returns the folder paths removed."""
    root = Path(ws_path).resolve()
    if not root.is_dir():
        return []
    removed: list[str] = []
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        path = Path(dirpath)
        if path == root:
            continue
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if _is_empty_dir(path):
            path.rmdir()
            removed.append("/".join(rel.parts))
    return removed


def remove_empty_tree(ws_path: str, folder: str) -> list[str]:
    """After a folder move, make the source vanish: remove ``folder``'s now-empty
    subtree bottom-up, then its emptied parents. Only touches empty dirs (a leftover
    file keeps its dir). Returns the folder paths removed."""
    root = Path(ws_path).resolve()
    removed: list[str] = []
    if folder:
        sub = root / Path(*folder.split("/"))
        if sub.is_dir():
            dirs = sorted(
                (p for p in sub.rglob("*") if p.is_dir()),
                key=lambda p: len(p.parts),
                reverse=True,
            )
            for path in [*dirs, sub]:
                if _is_empty_dir(path):
                    path.rmdir()
                    removed.append("/".join(path.relative_to(root).parts))
    parent = folder.rsplit("/", 1)[0] if "/" in folder else ""
    removed.extend(prune_empty_parents(ws_path, parent))
    return removed


def workspace_path(name: str, workspaces_dir: str | None = None, user_id: str | None = None) -> str:
    """Returns the filesystem path for a workspace directory."""
    base = Path(workspaces_dir or os.getenv("WORKSPACES_DIR", "/workspaces"))
    if user_id:
        base = base / user_id
    return str(base / name)


def list_workspaces(workspaces_dir: str | None = None, user_id: str | None = None) -> list[str]:
    base = Path(workspaces_dir or os.getenv("WORKSPACES_DIR", "/workspaces"))
    if user_id:
        base = base / user_id
    if not base.exists():
        return []
    return [p.name for p in base.iterdir() if p.is_dir()]


def note_filepath(ws_path: str, folder: str, title: str) -> str:
    filename = title_to_windows_filename(title) + ".md"
    parts = [p for p in folder.split("/") if p]
    return str(Path(ws_path, *parts, filename))


def write_note_file(
    path: str,
    note_id: str,
    title: str,
    tags: list[str],
    created_at: str,
    updated_at: str,
    content: str,
) -> None:
    post = frontmatter.Post(
        content,
        id=note_id,
        title=title,
        tags=tags,
        created_at=created_at,
        updated_at=updated_at,
    )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w") as f:
        frontmatter.dump(post, f)


def read_note_file(path: str) -> dict:
    post = frontmatter.load(path)
    return {
        "id": post.get("id"),
        "title": post.get("title"),
        "tags": post.get("tags", []),
        "created_at": post.get("created_at"),
        "updated_at": post.get("updated_at"),
        "content": post.content,
        "path": path,
    }


def scan_notes(workspace_path: str) -> list[dict]:
    ws = Path(workspace_path)
    if not ws.exists():
        return []
    results = []
    for p in sorted(ws.rglob("*.md")):
        if ".git" in p.parts:
            continue
        results.append(read_note_file(str(p)))
    return results


def create_workspace(
    name: str, workspaces_dir: str | None = None, user_id: str | None = None
) -> str:
    """Creates a new workspace directory with git repo. Returns the workspace path."""
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,49}$", name):
        raise ValueError(
            f"Invalid workspace name '{name}'."
            " Use letters, digits, hyphens, underscores (max 50 chars)."
        )

    ws_path = Path(workspace_path(name, workspaces_dir=workspaces_dir, user_id=user_id))

    if ws_path.exists():
        raise FileExistsError(f"Workspace '{name}' already exists.")

    ws_path.parent.mkdir(parents=True, exist_ok=True)
    GitRepository.init(str(ws_path))
    return str(ws_path)
