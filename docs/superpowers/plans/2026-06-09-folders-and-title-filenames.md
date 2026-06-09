# Folders and Title-Based Filenames Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat `notes/{id}-{slug}.md` storage layout with arbitrary-depth folders and human-readable Windows-safe filenames derived from note titles.

**Architecture:** Add a `folder` column to `notes` (Alembic migration), replace `title_to_slug` with `title_to_windows_filename`, rebuild path logic so the service derives file locations from `(ws_path, note.folder, note.title)` stored in the DB. Renames and folder moves use `git mv` to preserve history.

**Tech Stack:** Python/FastAPI, SQLModel, gitpython, FastMCP, pytest, Alembic + SQLite

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `src/kajet_turbo/workspace.py` | Modify | Replace `title_to_slug` → `title_to_windows_filename`; add `normalize_folder`; change `note_filepath` signature (drop `note_id`); update `scan_notes` to `rglob`; remove `notes/` from `create_workspace` |
| `src/kajet_turbo/git_ops.py` | Modify | Add `rename_file_commit` |
| `src/kajet_turbo/models.py` | Modify | Add `folder: str = Field(default="")` to `Note` |
| `alembic/versions/<new>.py` | Create | Add `folder TEXT NOT NULL DEFAULT ""` to `notes` |
| `src/kajet_turbo/repositories/notes.py` | Modify | `insert`/`update`/`list` gain `folder`; add `check_unique` |
| `src/kajet_turbo/services/notes.py` | Modify | All methods use folder+title path; `save` validates uniqueness; `update` calls `rename_file_commit` on path change |
| `src/kajet_turbo/mcp/notes.py` | Modify | `save_note`/`update_note` gain `folder` param |
| `src/kajet_turbo/api/workspaces.py` | Modify | List and detail endpoints return `folder`; list accepts `?folder=` |
| `tests/test_workspace.py` | Modify | Replace slug tests; update filepath/scan/create tests |
| `tests/test_api_workspaces.py` | Modify | Pass `folder` in service calls; assert `folder` in responses |

---

## Task 1: New filename and folder utilities in `workspace.py`

**Files:**
- Modify: `src/kajet_turbo/workspace.py`
- Test: `tests/test_workspace.py`

- [ ] **Step 1: Write failing tests**

Replace the entire `test_workspace.py` with the following (keep existing non-slug/filepath tests intact, replace the ones that change):

```python
import subprocess
import pytest
from pathlib import Path
from kajet_turbo.workspace import (
    list_workspaces,
    create_workspace,
    note_filepath,
    write_note_file,
    read_note_file,
    title_to_windows_filename,
    normalize_folder,
    scan_notes,
)


@pytest.fixture
def workspaces_dir(tmp_path):
    return tmp_path / "workspaces"


@pytest.fixture
def workspace(workspaces_dir):
    ws = workspaces_dir / "moj-projekt"
    ws.mkdir(parents=True)
    subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(ws), check=True, capture_output=True)
    return ws


def test_list_workspaces(workspace, workspaces_dir):
    (workspaces_dir / "drugi-projekt").mkdir()
    names = list_workspaces(str(workspaces_dir))
    assert "moj-projekt" in names
    assert "drugi-projekt" in names


def test_list_workspaces_with_user_id(tmp_path):
    user_dir = tmp_path / "u1"
    (user_dir / "ws-a").mkdir(parents=True)
    (user_dir / "ws-b").mkdir()
    names = list_workspaces(str(tmp_path), user_id="u1")
    assert set(names) == {"ws-a", "ws-b"}


# --- title_to_windows_filename ---

def test_title_to_windows_filename_strips_colon():
    assert title_to_windows_filename("Spotkanie: kickoff") == "Spotkanie kickoff"


def test_title_to_windows_filename_strips_all_forbidden():
    assert title_to_windows_filename('a"b*c?d<e>f|g') == "a b c d e f g"


def test_title_to_windows_filename_normalizes_multiple_spaces():
    assert title_to_windows_filename("a:::b") == "a b"


def test_title_to_windows_filename_keeps_unicode():
    assert title_to_windows_filename("Żółta łódź") == "Żółta łódź"


def test_title_to_windows_filename_reserved_con():
    assert title_to_windows_filename("CON") == "_CON"


def test_title_to_windows_filename_reserved_case_insensitive():
    assert title_to_windows_filename("nul") == "_nul"


def test_title_to_windows_filename_reserved_com1():
    assert title_to_windows_filename("COM1") == "_COM1"


def test_title_to_windows_filename_empty_returns_untitled():
    assert title_to_windows_filename("") == "untitled"


def test_title_to_windows_filename_all_forbidden_returns_untitled():
    assert title_to_windows_filename(":::") == "untitled"


def test_title_to_windows_filename_strips_trailing_dot():
    assert title_to_windows_filename("file.") == "file"


def test_title_to_windows_filename_truncates_to_200():
    assert len(title_to_windows_filename("a" * 300)) == 200


# --- normalize_folder ---

def test_normalize_folder_empty():
    assert normalize_folder("") == ""


def test_normalize_folder_basic():
    assert normalize_folder("Projekty/Klient A") == "Projekty/Klient A"


def test_normalize_folder_strips_leading_trailing_slash():
    assert normalize_folder("/foo/bar/") == "foo/bar"


def test_normalize_folder_strips_spaces():
    assert normalize_folder("  foo/bar  ") == "foo/bar"


def test_normalize_folder_rejects_dotdot():
    with pytest.raises(ValueError):
        normalize_folder("../etc")


def test_normalize_folder_rejects_dotdot_nested():
    with pytest.raises(ValueError):
        normalize_folder("foo/../bar")


def test_normalize_folder_sanitizes_forbidden_chars_in_segment():
    result = normalize_folder("Proj:ekt/Klient")
    assert result == "Proj ekt/Klient"


# --- note_filepath ---

def test_note_filepath_root():
    path = note_filepath("/ws", "", "My Note")
    assert path == str(Path("/ws/My Note.md"))


def test_note_filepath_with_folder():
    path = note_filepath("/ws", "Projekty/Klient A", "Spotkanie")
    assert path == str(Path("/ws/Projekty/Klient A/Spotkanie.md"))


def test_note_filepath_sanitizes_title():
    path = note_filepath("/ws", "", "Spotkanie: kickoff")
    assert path == str(Path("/ws/Spotkanie kickoff.md"))


# --- write/read ---

def test_write_and_read_note_file(workspace):
    path = note_filepath(str(workspace), "", "Test Note")
    write_note_file(
        path,
        note_id="abc1234",
        title="Test Note",
        tags=["python"],
        created_at="2026-06-08T12:00:00+00:00",
        updated_at="2026-06-08T12:00:00+00:00",
        content="# Hello\n\nTreść notatki.",
    )
    result = read_note_file(path)
    assert result["id"] == "abc1234"
    assert result["title"] == "Test Note"
    assert result["tags"] == ["python"]
    assert "Treść notatki" in result["content"]


def test_scan_notes_finds_all_including_subfolders(workspace):
    for i in range(2):
        path = note_filepath(str(workspace), "", f"Notatka {i}")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        write_note_file(path, f"id{i}", f"Notatka {i}", [], "2026-06-08T12:00:00+00:00", "2026-06-08T12:00:00+00:00", f"treść {i}")
    # note in subfolder
    path_sub = note_filepath(str(workspace), "Projekty", "Sub-notatka")
    Path(path_sub).parent.mkdir(parents=True, exist_ok=True)
    write_note_file(path_sub, "idsub", "Sub-notatka", [], "2026-06-08T12:00:00+00:00", "2026-06-08T12:00:00+00:00", "sub")
    notes = scan_notes(str(workspace))
    ids = [n["id"] for n in notes if n["id"]]
    assert set(ids) == {"id0", "id1", "idsub"}


def test_scan_notes_ignores_non_note_md(workspace):
    # Write a plain README without frontmatter id
    (workspace / "README.md").write_text("# Readme\n\nNo frontmatter here.")
    path = note_filepath(str(workspace), "", "Real Note")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    write_note_file(path, "r1", "Real Note", [], "2026-06-08T12:00:00+00:00", "2026-06-08T12:00:00+00:00", "content")
    notes = scan_notes(str(workspace))
    # README has no 'id' in frontmatter — still returned by scan_notes, filtered by caller
    ids = [n["id"] for n in notes if n["id"]]
    assert ids == ["r1"]


def test_create_workspace(tmp_path):
    ws_path = create_workspace("nowy-projekt", str(tmp_path))
    assert (tmp_path / "nowy-projekt").is_dir()
    assert (tmp_path / "nowy-projekt" / ".git").is_dir()
    assert ws_path == str(tmp_path / "nowy-projekt")


def test_create_workspace_with_user_id(tmp_path):
    ws_path = create_workspace("moj-ws", str(tmp_path), user_id="u42")
    assert (tmp_path / "u42" / "moj-ws" / ".git").is_dir()
    assert ws_path == str(tmp_path / "u42" / "moj-ws")


def test_create_workspace_rejects_invalid_name(tmp_path):
    with pytest.raises(ValueError):
        create_workspace("foo/bar", str(tmp_path))
    with pytest.raises(ValueError):
        create_workspace("", str(tmp_path))


def test_create_workspace_rejects_duplicate(tmp_path):
    create_workspace("duplikat", str(tmp_path))
    with pytest.raises(FileExistsError):
        create_workspace("duplikat", str(tmp_path))
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/erxyi/Projekty/kajet-turbo
uv run pytest tests/test_workspace.py -x -q 2>&1 | head -30
```

Expected: multiple ImportError / AttributeError failures (`title_to_windows_filename`, `normalize_folder` not found; `note_filepath` wrong signature).

- [ ] **Step 3: Implement the new functions in `workspace.py`**

Replace the entire content of `src/kajet_turbo/workspace.py`:

```python
import os
import re
from pathlib import Path

import frontmatter

WORKSPACES_DIR = os.getenv("WORKSPACES_DIR", "/workspaces")

_WINDOWS_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WINDOWS_RESERVED = re.compile(
    r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$', re.IGNORECASE
)


def title_to_windows_filename(title: str) -> str:
    result = _WINDOWS_FORBIDDEN.sub(' ', title)
    result = re.sub(r' +', ' ', result)
    result = result.strip().rstrip('. ')
    if _WINDOWS_RESERVED.match(result):
        result = '_' + result
    if not result:
        result = 'untitled'
    return result[:200]


def normalize_folder(folder: str) -> str:
    folder = folder.strip().strip('/')
    if not folder:
        return ''
    parts = [s for s in folder.split('/') if s]
    for part in parts:
        if part == '..':
            raise ValueError("Invalid folder: '..' not allowed")
    return '/'.join(title_to_windows_filename(p) for p in parts)


def workspace_path(name: str, workspaces_dir: str | None = None, user_id: str | None = None) -> str:
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
    ws = Path(workspace_path)
    if not ws.exists():
        return []
    results = []
    for p in sorted(ws.rglob("*.md")):
        if ".git" in p.parts:
            continue
        results.append(read_note_file(str(p)))
    return results


def create_workspace(name: str, workspaces_dir: str | None = None, user_id: str | None = None) -> str:
    import subprocess

    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,49}$", name):
        raise ValueError(f"Invalid workspace name '{name}'. Use letters, digits, hyphens, underscores (max 50 chars).")

    ws_path = Path(workspace_path(name, workspaces_dir=workspaces_dir, user_id=user_id))

    if ws_path.exists():
        raise FileExistsError(f"Workspace '{name}' already exists.")

    ws_path.mkdir(parents=True)
    subprocess.run(["git", "init", str(ws_path)], check=True, capture_output=True)
    return str(ws_path)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_workspace.py -x -q 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/kajet_turbo/workspace.py tests/test_workspace.py
git commit -m "feat: replace title_to_slug with title_to_windows_filename, add normalize_folder, remove notes/ subdir"
```

---

## Task 2: `rename_file_commit` in `git_ops.py`

**Files:**
- Modify: `src/kajet_turbo/git_ops.py`
- Test: `tests/test_workspace.py` (append test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_workspace.py`:

```python
def test_rename_file_commit(workspace):
    from kajet_turbo.git_ops import commit_file, rename_file_commit

    initial = workspace / "hello.md"
    initial.write_text("content")
    commit_file(str(workspace), "hello.md", "add hello")

    rename_file_commit(str(workspace), "hello.md", "world.md", "rename hello to world")

    assert not initial.exists()
    assert (workspace / "world.md").exists()
    assert (workspace / "world.md").read_text() == "content"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
uv run pytest tests/test_workspace.py::test_rename_file_commit -x -q
```

Expected: ImportError — `rename_file_commit` not found.

- [ ] **Step 3: Add `rename_file_commit` to `git_ops.py`**

```python
# src/kajet_turbo/git_ops.py
from git import Repo, InvalidGitRepositoryError, GitCommandError


class GitError(Exception):
    pass


def commit_file(workspace_path: str, relative_path: str, message: str) -> None:
    try:
        repo = Repo(workspace_path)
        repo.index.add([relative_path])
        repo.index.commit(message)
    except (InvalidGitRepositoryError, GitCommandError, FileNotFoundError) as e:
        raise GitError(str(e)) from e


def delete_file_commit(workspace_path: str, relative_path: str, message: str) -> None:
    try:
        repo = Repo(workspace_path)
        repo.index.remove([relative_path], working_tree=True)
        repo.index.commit(message)
    except (InvalidGitRepositoryError, GitCommandError) as e:
        raise GitError(str(e)) from e


def rename_file_commit(workspace_path: str, old_rel: str, new_rel: str, message: str) -> None:
    try:
        repo = Repo(workspace_path)
        repo.index.move([old_rel, new_rel])
        repo.index.commit(message)
    except (InvalidGitRepositoryError, GitCommandError, FileNotFoundError) as e:
        raise GitError(str(e)) from e
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
uv run pytest tests/test_workspace.py::test_rename_file_commit -x -q
```

Expected: PASS.

- [ ] **Step 5: Run full workspace test suite**

```bash
uv run pytest tests/test_workspace.py -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/kajet_turbo/git_ops.py tests/test_workspace.py
git commit -m "feat: add rename_file_commit to git_ops"
```

---

## Task 3: DB model and Alembic migration

**Files:**
- Modify: `src/kajet_turbo/models.py`
- Create: `alembic/versions/<revision>_add_folder_to_notes.py`

- [ ] **Step 1: Add `folder` field to `Note` in `models.py`**

In `src/kajet_turbo/models.py`, update the `Note` class:

```python
class Note(SQLModel, table=True):
    __tablename__ = "notes"

    id: str = Field(primary_key=True)
    workspace: str
    owner_id: str = Field(default="")
    title: str
    folder: str = Field(default="")
    tags: str | None = Field(default=None, sa_column=Column(Text))
    created_at: str
    updated_at: str
    fts_rowid: int | None = None
```

(Add `folder: str = Field(default="")` after `title`.)

- [ ] **Step 2: Generate the migration**

```bash
cd /Users/erxyi/Projekty/kajet-turbo
DB_PATH=/tmp/kajet-migrate-test.db uv run alembic revision --autogenerate -m "add folder to notes"
```

Expected output: `Generating .../alembic/versions/<hash>_add_folder_to_notes.py`

- [ ] **Step 3: Verify the generated migration**

Open the generated file. The `upgrade()` function must contain something like:

```python
def upgrade() -> None:
    with op.batch_alter_table('notes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('folder', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=''))
```

If the autogenerated migration looks correct, proceed. If `server_default` is missing, add it manually.

- [ ] **Step 4: Run migration on a test DB to verify**

```bash
DB_PATH=/tmp/kajet-test-folder.db uv run alembic upgrade head
```

Expected: runs without error, "Running upgrade ... -> <hash>, add folder to notes"

- [ ] **Step 5: Commit**

```bash
git add src/kajet_turbo/models.py alembic/versions/
git commit -m "feat: add folder column to notes (model + migration)"
```

---

## Task 4: Update `repositories/notes.py`

**Files:**
- Modify: `src/kajet_turbo/repositories/notes.py`

No separate unit tests for the repository — it's tested through the service layer in Task 5.

- [ ] **Step 1: Update `insert` to accept and save `folder`**

In `NoteRepository.insert`, add `folder: str = ""` parameter and pass it to the `Note` constructor:

```python
def insert(
    self,
    note_id: str,
    workspace: str,
    owner_id: str,
    title: str,
    tags: list[str],
    created_at: str,
    updated_at: str,
    content: str,
    folder: str = "",
) -> None:
    with Session(self._engine) as session:
        result = session.execute(
            text(
                "INSERT INTO notes_fts (note_id, workspace, title, content)"
                " VALUES (:note_id, :workspace, :title, :content)"
            ),
            {"note_id": note_id, "workspace": workspace, "title": title, "content": content},
        )
        fts_rowid = result.lastrowid
        note = Note(
            id=note_id,
            workspace=workspace,
            owner_id=owner_id,
            title=title,
            folder=folder,
            tags=json.dumps(tags),
            created_at=created_at,
            updated_at=updated_at,
            fts_rowid=fts_rowid,
        )
        session.add(note)
        session.commit()
```

- [ ] **Step 2: Update `update` to accept and save `folder`**

In `NoteRepository.update`, add `folder: str | None = None` parameter. After the existing `note.title = new_title` line, add:

```python
if folder is not None:
    note.folder = folder
```

Full updated signature:
```python
def update(
    self,
    note_id: str,
    owner_id: str | None = None,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    updated_at: str = "",
    folder: str | None = None,
) -> None:
```

Add inside the method body (after `note.tags = json.dumps(new_tags)`):
```python
if folder is not None:
    note.folder = folder
```

- [ ] **Step 3: Add `check_unique` method**

Add to `NoteRepository`:

```python
def check_unique(self, workspace: str, owner_id: str, folder: str, title: str) -> bool:
    """Returns True if no note with this (workspace, owner_id, folder, title) exists."""
    with Session(self._engine) as session:
        q = select(Note).where(
            Note.workspace == workspace,
            Note.owner_id == owner_id,
            Note.folder == folder,
            Note.title == title,
        )
        return session.exec(q).first() is None
```

- [ ] **Step 4: Update `list` to return `folder` and accept optional `folder` filter**

Update `NoteRepository.list`:

```python
def list(
    self,
    workspace: str,
    owner_id: str,
    tags: list[str] | None = None,
    limit: int = 20,
    folder: str | None = None,
) -> list[dict]:
    with Session(self._engine) as session:
        q = select(Note).where(Note.workspace == workspace, Note.owner_id == owner_id)
        if folder is not None:
            q = q.where(Note.folder == folder)
        rows = session.exec(q.order_by(Note.updated_at.desc())).all()

    result = []
    for note in rows:
        note_tags = json.loads(note.tags or "[]")
        if tags and not any(t in note_tags for t in tags):
            continue
        result.append({
            "note_id": note.id,
            "workspace": note.workspace,
            "owner_id": note.owner_id,
            "title": note.title,
            "folder": note.folder,
            "tags": note_tags,
            "created_at": note.created_at,
            "updated_at": note.updated_at,
        })
        if len(result) >= limit:
            break
    return result
```

- [ ] **Step 5: Run existing tests to confirm nothing is broken**

```bash
uv run pytest tests/ -q --tb=short 2>&1 | tail -20
```

Expected: all tests pass (or only pre-existing failures if any).

- [ ] **Step 6: Commit**

```bash
git add src/kajet_turbo/repositories/notes.py
git commit -m "feat: update NoteRepository — add folder support, check_unique, list filter"
```

---

## Task 5: Update `services/notes.py`

**Files:**
- Modify: `src/kajet_turbo/services/notes.py`
- Test: `tests/test_api_workspaces.py` (extend existing tests)

- [ ] **Step 1: Write failing tests in `test_api_workspaces.py`**

Append these tests to `tests/test_api_workspaces.py`:

```python
def test_save_note_creates_file_in_folder(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Moja notatka", "content", [], folder="Projekty/Klient A")["note_id"]
    expected = Path(ws_path) / "Projekty" / "Klient A" / "Moja notatka.md"
    assert expected.exists()


def test_save_note_root_no_notes_subdir(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Root notatka", "content", [])["note_id"]
    expected = Path(ws_path) / "Root notatka.md"
    assert expected.exists()
    assert not (Path(ws_path) / "notes").exists()


def test_save_note_duplicate_title_same_folder_raises(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Dup", "content", [], folder="F")
    with pytest.raises(ValueError, match="już istnieje"):
        note_svc.save("u1", "test-ws", ws_path, "Dup", "content2", [], folder="F")


def test_update_note_title_renames_file(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Stary tytuł", "content", [])["note_id"]
    old_path = Path(ws_path) / "Stary tytuł.md"
    assert old_path.exists()

    note_svc.update(note_id, owner_id="u1", ws_path=ws_path, title="Nowy tytuł")
    assert not old_path.exists()
    assert (Path(ws_path) / "Nowy tytuł.md").exists()


def test_update_note_folder_moves_file(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Przenoszona", "content", [])["note_id"]

    note_svc.update(note_id, owner_id="u1", ws_path=ws_path, folder="Archiwum")
    assert not (Path(ws_path) / "Przenoszona.md").exists()
    assert (Path(ws_path) / "Archiwum" / "Przenoszona.md").exists()


def test_get_with_content_uses_db_path(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Notatka get", "treść", [], folder="Docs")["note_id"]
    result = note_svc.get_with_content(note_id, owner_id="u1", ws_path=ws_path)
    assert result is not None
    assert result["folder"] == "Docs"
    assert result["content"] == "treść"


def test_delete_uses_db_path(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "Do usunięcia", "content", [], folder="Trash")["note_id"]
    filepath = Path(ws_path) / "Trash" / "Do usunięcia.md"
    assert filepath.exists()

    note_svc.delete(note_id, owner_id="u1", ws_path=ws_path)
    assert not filepath.exists()


def test_reindex_finds_notes_in_subfolders(auth_client):
    client, note_svc, ws_path = auth_client
    # Create files directly (bypass service)
    from kajet_turbo.workspace import note_filepath, write_note_file
    p1 = note_filepath(ws_path, "", "Root note")
    write_note_file(p1, "rid1", "Root note", [], "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "r")
    p2 = note_filepath(ws_path, "Sub", "Sub note")
    write_note_file(p2, "sid1", "Sub note", [], "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "s")

    result = note_svc.reindex("test-ws", owner_id="u1", ws_path=ws_path)
    assert result["count"] == 2
```

Note: add `import pytest` at the top of `test_api_workspaces.py` if not already present.

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
uv run pytest tests/test_api_workspaces.py -k "test_save_note_creates_file_in_folder or test_save_note_root_no_notes_subdir or test_save_note_duplicate or test_update_note_title or test_update_note_folder or test_get_with_content_uses_db or test_delete_uses_db or test_reindex_finds" -x -q 2>&1 | head -30
```

Expected: failures (wrong signatures, missing `folder` support).

- [ ] **Step 3: Rewrite `services/notes.py`**

```python
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from nanoid import generate

from kajet_turbo.git_ops import GitError, commit_file, delete_file_commit, rename_file_commit
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
            commit_file(ws_path, relative, f"note: add {title}")
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

        try:
            if old_path != new_path:
                Path(new_path).parent.mkdir(parents=True, exist_ok=True)
                rename_file_commit(ws_path, old_rel, new_rel, f"note: rename to {new_title}")
                write_note_file(new_path, note_id, new_title, new_tags, note.created_at, now, new_content)
                commit_file(ws_path, new_rel, f"note: update {new_title}")
            else:
                write_note_file(old_path, note_id, new_title, new_tags, note.created_at, now, new_content)
                commit_file(ws_path, old_rel, f"note: update {new_title}")
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
            delete_file_commit(ws_path, relative, f"note: delete {note_id}")
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
```

- [ ] **Step 4: Update `auth_client` fixture in `test_api_workspaces.py`**

The `workspace` fixture creates `(ws / "notes").mkdir(parents=True)`. Remove the `/ "notes"` part and `"notes"` mkdir since workspace root is now the notes root:

```python
@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspaces" / "u1" / "test-ws"
    ws.mkdir(parents=True)
    subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(ws), check=True, capture_output=True)
    return ws
```

(Remove the `(ws / "notes").mkdir(parents=True)` line — only `ws.mkdir(parents=True)` remains.)

Also update any existing tests that call `note_svc.save(...)` — they now work unchanged since `folder=""` is the default.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/ -q --tb=short 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/kajet_turbo/services/notes.py tests/test_api_workspaces.py
git commit -m "feat: update NoteService — folder support, title-based paths, rename on move"
```

---

## Task 6: Update `mcp/notes.py`

**Files:**
- Modify: `src/kajet_turbo/mcp/notes.py`

No new tests needed — MCP tools delegate to `NoteService` which is already tested.

- [ ] **Step 1: Add `folder` parameter to `save_note`**

In `mcp/notes.py`, update the `save_note` tool signature:

```python
@mcp.tool()
@logged_tool
async def save_note(
    title: str,
    content: str,
    ctx: Context,
    tags: list[str] | None = None,
    folder: str = "",
) -> str:
    """Zapisuje nową notatkę w podanym folderze (domyślnie root).
    folder: opcjonalna ścieżka np. 'Projekty/Klient A'.
    Sukces: {"note_id": "..."}. Błąd: {"error": "..."}."""
    try:
        owner_id, ws_name, ws_path = await get_active_workspace(ctx, workspace_service)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})
    try:
        result = note_service.save(owner_id, ws_name, ws_path, title, content, tags or [], folder=folder)
    except (GitError, ValueError) as e:
        return json.dumps({"error": str(e)})
    return json.dumps(result)
```

- [ ] **Step 2: Add `folder` parameter to `update_note`**

```python
@mcp.tool()
@logged_tool
async def update_note(
    note_id: str,
    ctx: Context,
    title: str | None = None,
    content: str | None = None,
    tags: list[str] | None = None,
    folder: str | None = None,
) -> str:
    """Aktualizuje notatkę. folder opcjonalny — jeśli podany, przenosi notatkę do nowego folderu.
    Sukces: {"note_id": "..."}. Błąd: {"error": "..."}."""
    try:
        owner_id, _, ws_path = await get_active_workspace(ctx, workspace_service)
    except RuntimeError as e:
        return json.dumps({"error": str(e)})
    try:
        result = note_service.update(note_id, owner_id=owner_id, ws_path=ws_path,
                                     title=title, content=content, tags=tags, folder=folder)
    except (ValueError, FileNotFoundError) as e:
        return json.dumps({"error": str(e)})
    except GitError as e:
        return json.dumps({"error": str(e)})
    return json.dumps(result)
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/ -q --tb=short 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/kajet_turbo/mcp/notes.py
git commit -m "feat: add folder param to save_note and update_note MCP tools"
```

---

## Task 7: Update `api/workspaces.py`

**Files:**
- Modify: `src/kajet_turbo/api/workspaces.py`
- Test: `tests/test_api_workspaces.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_api_workspaces.py`:

```python
def test_list_notes_returns_folder(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Notatka z folderem", "c", [], folder="Docs")
    resp = client.get("/api/workspaces/test-ws/notes")
    assert resp.status_code == 200
    notes = resp.json()["notes"]
    assert any(n["folder"] == "Docs" for n in notes)


def test_list_notes_folder_filter(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "W Docs", "c", [], folder="Docs")
    note_svc.save("u1", "test-ws", ws_path, "W root", "c", [])
    resp = client.get("/api/workspaces/test-ws/notes?folder=Docs")
    notes = resp.json()["notes"]
    assert len(notes) == 1
    assert notes[0]["title"] == "W Docs"


def test_html_returns_folder(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "HTML test", "# Hello", [], folder="F")["note_id"]
    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/html")
    assert resp.status_code == 200
    assert resp.json()["folder"] == "F"


def test_markdown_returns_folder(auth_client):
    client, note_svc, ws_path = auth_client
    note_id = note_svc.save("u1", "test-ws", ws_path, "MD test", "content", [], folder="G")["note_id"]
    resp = client.get(f"/api/workspaces/test-ws/notes/{note_id}/markdown")
    assert resp.status_code == 200
    assert resp.json()["folder"] == "G"
```

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
uv run pytest tests/test_api_workspaces.py -k "test_list_notes_returns_folder or test_list_notes_folder_filter or test_html_returns_folder or test_markdown_returns_folder" -x -q
```

Expected: failures (missing `folder` in responses).

- [ ] **Step 3: Update `api/workspaces.py`**

1. Add `folder: str | None = None` query param to `api_list_notes`:

```python
@router.get("/api/workspaces/{name}/notes")
async def api_list_notes(
    name: str,
    request: Request,
    folder: str | None = None,
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    notes = note_service.list(name, owner_id=user["id"], folder=folder)
    return JSONResponse({"notes": notes})
```

2. Add `"folder": note["folder"]` to `api_get_note_html` response:

```python
return JSONResponse({
    "note_id": note["note_id"],
    "title": note["title"],
    "folder": note["folder"],
    "tags": note["tags"],
    "created_at": note["created_at"],
    "updated_at": note["updated_at"],
    "content_html": _render_html(note["content"]),
})
```

3. Add `"folder": note["folder"]` to `api_get_note_markdown` response:

```python
return JSONResponse({
    "note_id": note["note_id"],
    "title": note["title"],
    "folder": note["folder"],
    "tags": note["tags"],
    "created_at": note["created_at"],
    "updated_at": note["updated_at"],
    "content": note["content"],
})
```

- [ ] **Step 4: Run all tests**

```bash
uv run pytest tests/ -q --tb=short 2>&1 | tail -20
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/kajet_turbo/api/workspaces.py tests/test_api_workspaces.py
git commit -m "feat: expose folder in notes API — list filter, html/markdown responses"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `title_to_windows_filename` replacing `title_to_slug` | Task 1 |
| `normalize_folder` with path traversal protection | Task 1 |
| `note_filepath(ws_path, folder, title)` | Task 1 |
| `scan_notes` recursive `rglob("*.md")` | Task 1 |
| `create_workspace` no longer creates `notes/` subdir | Task 1 |
| `rename_file_commit` in git_ops | Task 2 |
| `folder` column in `Note` model | Task 3 |
| Alembic migration | Task 3 |
| `NoteRepository.insert/update/list/check_unique` | Task 4 |
| `NoteService.save` with folder + uniqueness check | Task 5 |
| `NoteService.update` with git mv on path change | Task 5 |
| `NoteService.get_with_content/delete` use DB path | Task 5 |
| `NoteService.reindex` scans `**/*.md`, ignores no-id files | Task 5 |
| MCP `save_note`/`update_note` accept `folder` | Task 6 |
| API list returns `folder`, supports `?folder=` filter | Task 7 |
| API html/markdown responses include `folder` | Task 7 |

All requirements covered. ✓
