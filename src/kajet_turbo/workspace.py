import os
import re
from pathlib import Path

import frontmatter

WORKSPACES_DIR = os.getenv("WORKSPACES_DIR", "/workspaces")


def list_workspaces(workspaces_dir: str | None = None, user_id: str | None = None) -> list[str]:
    base = Path(workspaces_dir or os.getenv("WORKSPACES_DIR", "/workspaces"))
    if user_id:
        base = base / user_id
    if not base.exists():
        return []
    return [p.name for p in base.iterdir() if p.is_dir()]


def title_to_slug(title: str) -> str:
    slug = title.lower()
    slug = re.sub(r"[^\w\s-]", "", slug, flags=re.ASCII)  # ASCII-only: strips non-ASCII chars like ż, ó
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug.strip("-")
    return slug[:50]


def note_filepath(workspace_path: str, note_id: str, title: str) -> str:
    slug = title_to_slug(title)
    filename = f"{note_id}-{slug}.md"
    return str(Path(workspace_path) / "notes" / filename)


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
    with open(path, "w") as f:
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
    notes_dir = Path(workspace_path) / "notes"
    if not notes_dir.exists():
        return []
    return [read_note_file(str(p)) for p in sorted(notes_dir.glob("*.md"))]


def create_workspace(name: str, workspaces_dir: str | None = None, user_id: str | None = None) -> str:
    """Creates a new workspace directory with git repo. Returns the workspace path."""
    import subprocess

    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,49}$", name):
        raise ValueError(f"Invalid workspace name '{name}'. Use letters, digits, hyphens, underscores (max 50 chars).")

    base = Path(workspaces_dir or os.getenv("WORKSPACES_DIR", "/workspaces"))
    if user_id:
        base = base / user_id
    ws_path = base / name

    if ws_path.exists():
        raise FileExistsError(f"Workspace '{name}' already exists.")

    (ws_path / "notes").mkdir(parents=True)
    subprocess.run(["git", "init", str(ws_path)], check=True, capture_output=True)
    return str(ws_path)
