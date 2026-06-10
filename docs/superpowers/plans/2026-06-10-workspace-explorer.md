# Workspace Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat notes list with a 3-panel file explorer (folder tree + notes list + note preview) accessible at `/workspace/[slug]/notes/[[...path]]`.

**Architecture:** New backend `/ls` endpoint returns folder structure; existing `/notes?folder=X` serves the middle panel; SvelteKit optional catch-all route parses the URL to determine current folder and selected note. Three co-located Svelte components handle the three panels.

**Tech Stack:** FastAPI (Python), SvelteKit 2 / Svelte 5, SQLModel/SQLite, orval (API type generation)

---

## File Map

**Backend — create:**
- `src/kajet_turbo/api/schemas.py` — add `LsEntry`, `LsResponse`; add `size_bytes` to `NoteItem`

**Backend — modify:**
- `src/kajet_turbo/api/workspaces.py` — fix async→def on GET endpoints; add `/ls` endpoint; enrich `/notes` with `size_bytes`
- `src/kajet_turbo/repositories/notes.py` — add `list_folders()`

**Tests — modify:**
- `tests/test_api_workspaces.py` — tests for `size_bytes` in notes list, full `/ls` coverage

**Frontend — delete:**
- `frontend/src/routes/(protected)/workspace/[slug]/notes/+page.svelte`
- `frontend/src/routes/(protected)/workspace/[slug]/notes/+page.ts`

**Frontend — create:**
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/+page.ts`
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/+page.svelte`
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/FolderTree.svelte`
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/NotesList.svelte`
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/NotePreview.svelte`

---

## Task 1: Fix async anti-pattern — GET endpoints to `def`

**Files:**
- Modify: `src/kajet_turbo/api/workspaces.py`

FastAPI runs `def` handlers in a thread pool automatically. GET endpoints call sync SQLAlchemy/pathlib — they must not be `async def`.

- [ ] **Step 1: Change GET endpoints from `async def` to `def`**

In `src/kajet_turbo/api/workspaces.py`, change the signature of every GET endpoint. POST endpoints that use `await request.json()` stay `async def`:

```python
# BEFORE:
@router.get("/api/workspaces", response_model=WorkspacesListResponse)
async def api_list_workspaces(request: Request, ...):

# AFTER:
@router.get("/api/workspaces", response_model=WorkspacesListResponse)
def api_list_workspaces(request: Request, ...):
```

Apply the same change to: `api_list_notes`, `api_get_note_html`, `api_get_note_markdown`, `api_note_history`, `api_note_version`.

Keep `api_create_workspace` and `api_restore_note_version` as `async def` (they use `await request.json()`).

- [ ] **Step 2: Run existing tests to verify nothing broke**

```bash
uv run pytest tests/test_api_workspaces.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/kajet_turbo/api/workspaces.py
git commit -m "refactor: change sync GET endpoints from async def to def"
```

---

## Task 2: Add `list_folders` to NoteRepository and NoteService

**Files:**
- Modify: `src/kajet_turbo/repositories/notes.py`
- Modify: `src/kajet_turbo/services/notes.py`
- Modify: `tests/test_api_workspaces.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_workspaces.py`:

```python
def test_list_folders_returns_distinct_folders(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "N1", "c", [], folder="docs")
    note_svc.save("u1", "test-ws", ws_path, "N2", "c", [], folder="docs/guide")
    note_svc.save("u1", "test-ws", ws_path, "N3", "c", [], folder="notes")
    note_svc.save("u1", "test-ws", ws_path, "N4", "c", [])  # root — no folder

    folders = note_svc.list_folders("test-ws", "u1")

    assert sorted(folders) == ["docs", "docs/guide", "notes"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_api_workspaces.py::test_list_folders_returns_distinct_folders -v
```

Expected: `AttributeError: 'NoteService' object has no attribute 'list_folders'`

- [ ] **Step 3: Implement `list_folders` in NoteRepository**

Add to `src/kajet_turbo/repositories/notes.py` (after the `list` method):

```python
def list_folders(self, workspace: str, owner_id: str) -> list[str]:
    with Session(self._engine) as session:
        rows = session.execute(
            text(
                "SELECT DISTINCT folder FROM notes"
                " WHERE workspace = :workspace AND owner_id = :owner_id AND folder != ''"
            ),
            {"workspace": workspace, "owner_id": owner_id},
        ).fetchall()
    return [row[0] for row in rows]
```

- [ ] **Step 4: Add `list_folders` to NoteService**

Add to `src/kajet_turbo/services/notes.py` (after the `list` method):

```python
def list_folders(self, ws_name: str, owner_id: str) -> list[str]:
    return self._repo.list_folders(ws_name, owner_id)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_api_workspaces.py::test_list_folders_returns_distinct_folders -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/kajet_turbo/repositories/notes.py src/kajet_turbo/services/notes.py tests/test_api_workspaces.py
git commit -m "feat: add list_folders to NoteRepository and NoteService"
```

---

## Task 3: Add `size_bytes` to `NoteItem` and `/notes` endpoint

**Files:**
- Modify: `src/kajet_turbo/api/schemas.py`
- Modify: `src/kajet_turbo/api/workspaces.py`
- Modify: `tests/test_api_workspaces.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api_workspaces.py`:

```python
def test_list_notes_includes_size_bytes(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Sized Note", "hello world", [])
    resp = client.get("/api/workspaces/test-ws/notes")
    assert resp.status_code == 200
    note = resp.json()["notes"][0]
    assert "size_bytes" in note
    assert isinstance(note["size_bytes"], int)
    assert note["size_bytes"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_api_workspaces.py::test_list_notes_includes_size_bytes -v
```

Expected: FAIL — `AssertionError: assert 'size_bytes' in {...}`

- [ ] **Step 3: Add `size_bytes` to `NoteItem` schema**

In `src/kajet_turbo/api/schemas.py`, update `NoteItem`:

```python
class NoteItem(BaseModel):
    note_id: str
    workspace: str
    owner_id: str
    title: str
    folder: str
    tags: list[str]
    created_at: str
    updated_at: str
    size_bytes: int
```

- [ ] **Step 4: Update `api_list_notes` to compute `size_bytes`**

In `src/kajet_turbo/api/workspaces.py`, add import at the top of the file (after existing imports):

```python
from pathlib import Path
from kajet_turbo.workspace import note_filepath
```

Then update `api_list_notes`:

```python
@router.get("/api/workspaces/{name}/notes", response_model=NotesListResponse)
def api_list_notes(
    name: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
    folder: str | None = None,
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    ws_path = ws_service.workspace_path(user["id"], name)
    notes = note_service.list(name, owner_id=user["id"], folder=folder, limit=1000)
    enriched = []
    for note in notes:
        filepath = note_filepath(ws_path, note["folder"], note["title"])
        try:
            size_bytes = Path(filepath).stat().st_size
        except OSError:
            size_bytes = 0
        enriched.append({**note, "size_bytes": size_bytes})
    return JSONResponse({"notes": enriched})
```

- [ ] **Step 5: Run test to verify it passes**

```bash
uv run pytest tests/test_api_workspaces.py::test_list_notes_includes_size_bytes -v
```

Expected: PASS

- [ ] **Step 6: Run full test suite to catch regressions**

```bash
uv run pytest tests/test_api_workspaces.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/kajet_turbo/api/schemas.py src/kajet_turbo/api/workspaces.py tests/test_api_workspaces.py
git commit -m "feat: add size_bytes to NoteItem in /notes endpoint"
```

---

## Task 4: Add `/ls` endpoint

**Files:**
- Modify: `src/kajet_turbo/api/schemas.py`
- Modify: `src/kajet_turbo/api/workspaces.py`
- Modify: `tests/test_api_workspaces.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api_workspaces.py`:

```python
def test_ls_root_returns_folders_and_entries(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Root Note", "c", [])
    note_svc.save("u1", "test-ws", ws_path, "Deep Note", "c", [], folder="docs")

    resp = client.get("/api/workspaces/test-ws/ls")
    assert resp.status_code == 200
    data = resp.json()
    assert "docs" in data["folders"]
    assert len(data["entries"]) == 1
    assert data["entries"][0]["title"] == "Root Note"
    assert "size_bytes" in data["entries"][0]
    assert "note_id" in data["entries"][0]


def test_ls_subfolder_returns_notes_in_folder(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "Doc", "c", [], folder="docs")
    note_svc.save("u1", "test-ws", ws_path, "Root", "c", [])

    resp = client.get("/api/workspaces/test-ws/ls?path=docs")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entries"]) == 1
    assert data["entries"][0]["title"] == "Doc"


def test_ls_nonexistent_path_returns_404(auth_client):
    client, note_svc, ws_path = auth_client
    resp = client.get("/api/workspaces/test-ws/ls?path=nonexistent")
    assert resp.status_code == 404


def test_ls_recursive_returns_all_expanded_folders(auth_client):
    client, note_svc, ws_path = auth_client
    note_svc.save("u1", "test-ws", ws_path, "N1", "c", [], folder="docs/guide")
    note_svc.save("u1", "test-ws", ws_path, "N2", "c", [], folder="notes")

    resp = client.get("/api/workspaces/test-ws/ls?recursive=true")
    assert resp.status_code == 200
    data = resp.json()
    assert "docs" in data["folders"]
    assert "docs/guide" in data["folders"]
    assert "notes" in data["folders"]
    assert data["entries"] == []


def test_ls_returns_401_when_not_logged_in(anon_client):
    resp = anon_client.get("/api/workspaces/test-ws/ls")
    assert resp.status_code == 401


def test_ls_returns_403_when_no_access(no_access_client):
    resp = no_access_client.get("/api/workspaces/test-ws/ls")
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_api_workspaces.py::test_ls_root_returns_folders_and_entries tests/test_api_workspaces.py::test_ls_recursive_returns_all_expanded_folders -v
```

Expected: FAIL — `404 Not Found` (endpoint doesn't exist yet)

- [ ] **Step 3: Add `LsEntry` and `LsResponse` to schemas**

In `src/kajet_turbo/api/schemas.py`, add after `WorkspacesListResponse`:

```python
class LsEntry(BaseModel):
    note_id: str
    title: str
    size_bytes: int
    updated_at: str


class LsResponse(BaseModel):
    folders: list[str]
    entries: list[LsEntry]
```

- [ ] **Step 4: Add `/ls` endpoint to `workspaces.py`**

In `src/kajet_turbo/api/workspaces.py`, update the schemas import line to include the new types:

```python
from kajet_turbo.api.schemas import (
    LsEntry, LsResponse,
    NoteHistoryResponse, NoteHtmlResponse, NoteMarkdownResponse,
    NotesListResponse, WorkspacesListResponse,
)
```

Then add the endpoint after `api_list_notes`:

```python
@router.get("/api/workspaces/{name}/ls", response_model=LsResponse)
def api_ls(
    name: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
    note_service: NoteService = Depends(get_note_service),
    path: str = "",
    recursive: bool = False,
) -> JSONResponse:
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)

    ws_path = ws_service.workspace_path(user["id"], name)
    folder_abs = Path(ws_path, *path.split("/")) if path else Path(ws_path)

    if path and not folder_abs.is_dir():
        return JSONResponse({"error": "Folder not found"}, status_code=404)

    if recursive:
        all_folders = note_service.list_folders(name, user["id"])
        expanded: set[str] = set()
        for folder in all_folders:
            parts = folder.split("/")
            for i in range(1, len(parts) + 1):
                expanded.add("/".join(parts[:i]))
        return JSONResponse({"folders": sorted(expanded), "entries": []})

    subdirs = sorted(
        d.name for d in folder_abs.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )
    notes = note_service.list(name, owner_id=user["id"], folder=path, limit=1000)
    entries = []
    for note in notes:
        filepath = note_filepath(ws_path, note["folder"], note["title"])
        try:
            size_bytes = Path(filepath).stat().st_size
        except OSError:
            size_bytes = 0
        entries.append({
            "note_id": note["note_id"],
            "title": note["title"],
            "size_bytes": size_bytes,
            "updated_at": note["updated_at"],
        })
    return JSONResponse({"folders": subdirs, "entries": entries})
```

- [ ] **Step 5: Run all new tests**

```bash
uv run pytest tests/test_api_workspaces.py::test_ls_root_returns_folders_and_entries tests/test_api_workspaces.py::test_ls_subfolder_returns_notes_in_folder tests/test_api_workspaces.py::test_ls_nonexistent_path_returns_404 tests/test_api_workspaces.py::test_ls_recursive_returns_all_expanded_folders tests/test_api_workspaces.py::test_ls_returns_401_when_not_logged_in tests/test_api_workspaces.py::test_ls_returns_403_when_no_access -v
```

Expected: all 6 PASS.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest tests/test_api_workspaces.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/kajet_turbo/api/schemas.py src/kajet_turbo/api/workspaces.py src/kajet_turbo/repositories/notes.py tests/test_api_workspaces.py
git commit -m "feat: add /ls endpoint for folder tree exploration"
```

---

## Task 5: Regenerate orval API types

**Files:**
- Modify: `frontend/src/lib/api/index.ts`

- [ ] **Step 1: Start the backend server**

```bash
uv run uvicorn kajet_turbo.server:app --reload --port 8000
```

Leave this running in a separate terminal.

- [ ] **Step 2: Regenerate types**

```bash
cd frontend && npm run generate-api
```

Expected: `frontend/src/lib/api/index.ts` updated with `LsEntry`, `LsResponse`, `size_bytes` in `NoteItem`, and a new `apiLsApiWorkspacesNameLsGet` function (exact name may vary by orval config).

- [ ] **Step 3: Verify types compile**

```bash
cd frontend && npm run check
```

Expected: no type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api/index.ts
git commit -m "chore: regenerate orval types for /ls endpoint and size_bytes"
```

---

## Task 6: New SvelteKit route `[[...path]]` with `+page.ts`

**Files:**
- Delete: `frontend/src/routes/(protected)/workspace/[slug]/notes/+page.svelte`
- Delete: `frontend/src/routes/(protected)/workspace/[slug]/notes/+page.ts`
- Create: `frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/+page.ts`

- [ ] **Step 1: Delete the old route files**

```bash
rm "frontend/src/routes/(protected)/workspace/[slug]/notes/+page.svelte"
rm "frontend/src/routes/(protected)/workspace/[slug]/notes/+page.ts"
```

- [ ] **Step 2: Create the directory**

```bash
mkdir -p "frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]"
```

- [ ] **Step 3: Create `+page.ts`**

Create `frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/+page.ts`:

```typescript
import { redirect } from '@sveltejs/kit'
import type { PageLoad } from './$types'

export const load: PageLoad = async ({ params, fetch }) => {
  const slug = params.slug
  const segments = params.path ? params.path.split('/').filter(Boolean) : []

  // Disambiguate: try full path as a folder via /ls
  const lsUrl = `/api/workspaces/${slug}/ls?path=${segments.join('/')}`
  const lsResult = await fetch(lsUrl, { credentials: 'include' }).catch(() => null)

  if (lsResult?.status === 401) redirect(307, '/login')
  if (lsResult?.status === 403) redirect(307, '/workspaces')

  const isFolder = lsResult?.ok ?? true
  const folderPath = isFolder ? segments.join('/') : segments.slice(0, -1).join('/')
  const noteId = isFolder ? null : (segments.at(-1) ?? null)

  // Parallel: full tree for sidebar + notes in folder + note content (if selected)
  const notesUrl = `/api/workspaces/${slug}/notes?folder=${folderPath}`
  const treeUrl = `/api/workspaces/${slug}/ls?recursive=true`

  const [notesResult, treeResult, noteResult] = await Promise.all([
    fetch(notesUrl, { credentials: 'include' }).catch(() => null),
    fetch(treeUrl, { credentials: 'include' }).catch(() => null),
    noteId
      ? fetch(`/api/workspaces/${slug}/notes/${noteId}/html`, { credentials: 'include' }).catch(() => null)
      : Promise.resolve(null),
  ])

  const notes = notesResult?.ok ? (await notesResult.json()).notes : []
  const tree = treeResult?.ok ? await treeResult.json() : { folders: [], entries: [] }
  const note = noteResult?.ok ? await noteResult.json() : null

  return { notes, tree, folderPath, noteId, slug }
}
```

- [ ] **Step 4: Verify routing compiles**

```bash
cd frontend && npm run check
```

Expected: no errors. SvelteKit will warn about missing `+page.svelte` — that's fine, we add it next.

- [ ] **Step 5: Commit**

```bash
git add "frontend/src/routes/(protected)/workspace/[slug]/notes/"
git commit -m "feat: replace notes route with [[...path]] catch-all, add page.ts"
```

---

## Task 7: `FolderTree.svelte` component

**Files:**
- Create: `frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/FolderTree.svelte`

The component receives a flat list of all folder paths (e.g. `["docs", "docs/guide", "notes"]`) and renders a collapsible tree. Navigation uses SvelteKit `goto()`.

- [ ] **Step 1: Create `FolderTree.svelte`**

```svelte
<script lang="ts">
  import { goto } from '$app/navigation'

  let { folders, currentFolder, slug }: {
    folders: string[]
    currentFolder: string
    slug: string
  } = $props()

  // Build tree: { name, fullPath, children[] }
  type TreeNode = { name: string; fullPath: string; children: TreeNode[] }

  function buildTree(paths: string[]): TreeNode[] {
    const root: TreeNode[] = []
    const map = new Map<string, TreeNode>()

    for (const path of [...paths].sort()) {
      const parts = path.split('/')
      const name = parts.at(-1)!
      const node: TreeNode = { name, fullPath: path, children: [] }
      map.set(path, node)
      const parentPath = parts.slice(0, -1).join('/')
      if (parentPath && map.has(parentPath)) {
        map.get(parentPath)!.children.push(node)
      } else {
        root.push(node)
      }
    }
    return root
  }

  let tree = $derived(buildTree(folders))

  // Expanded state: set of fullPaths that are open
  let expanded = $state<Set<string>>(new Set(
    currentFolder
      ? currentFolder.split('/').map((_, i, arr) => arr.slice(0, i + 1).join('/'))
      : []
  ))

  function toggle(path: string) {
    const next = new Set(expanded)
    next.has(path) ? next.delete(path) : next.add(path)
    expanded = next
  }

  function navigate(folder: string) {
    goto(`/workspace/${slug}/notes/${folder}`)
  }
</script>

{#snippet node(n: TreeNode)}
  <li>
    <button
      class="folder-row"
      class:active={currentFolder === n.fullPath}
      onclick={() => { toggle(n.fullPath); navigate(n.fullPath) }}
    >
      <span class="folder-chevron">{expanded.has(n.fullPath) ? '▼' : '▶'}</span>
      <span class="folder-name">{n.name}/</span>
    </button>
    {#if expanded.has(n.fullPath) && n.children.length > 0}
      <ul class="subtree">
        {#each n.children as child}
          {@render node(child)}
        {/each}
      </ul>
    {/if}
  </li>
{/snippet}

<nav class="folder-tree">
  <button
    class="folder-row root-row"
    class:active={currentFolder === ''}
    onclick={() => navigate('')}
  >
    <span class="folder-name">{slug}</span>
  </button>
  <ul class="tree-root">
    {#each tree as n}
      {@render node(n)}
    {/each}
  </ul>
</nav>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .folder-tree {
    font-family: v.$font-mono;
    font-size: 0.82rem;
    overflow-y: auto;
    height: 100%;
  }

  ul {
    list-style: none;
    padding: 0;
    margin: 0;
  }

  .subtree {
    padding-left: 12px;
  }

  .folder-row {
    display: flex;
    align-items: center;
    gap: 4px;
    width: 100%;
    padding: 3px 12px;
    background: none;
    border: none;
    color: v.$text-muted;
    cursor: pointer;
    text-align: left;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;

    &:hover { color: v.$text-primary; }
    &.active { color: v.$accent; }
  }

  .root-row {
    padding: 4px 12px;
    font-size: 0.75rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: v.$text-muted;
    margin-bottom: 4px;
  }

  .folder-chevron {
    font-size: 0.6rem;
    width: 10px;
    flex-shrink: 0;
  }
</style>
```

- [ ] **Step 2: Verify no TypeScript errors**

```bash
cd frontend && npm run check
```

Expected: no errors (fix any snippet type issues if they arise by removing the type annotation from the snippet parameter).

- [ ] **Step 3: Commit**

```bash
git add "frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/FolderTree.svelte"
git commit -m "feat: add FolderTree sidebar component"
```

---

## Task 8: `NotesList.svelte` component

**Files:**
- Create: `frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/NotesList.svelte`

- [ ] **Step 1: Create `NotesList.svelte`**

```svelte
<script lang="ts">
  import { goto } from '$app/navigation'
  import type { NoteItem } from '$lib/api'

  let { notes, currentNoteId, folderPath, slug }: {
    notes: NoteItem[]
    currentNoteId: string | null
    folderPath: string
    slug: string
  } = $props()

  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  function formatDate(iso: string): string {
    if (!iso) return ''
    return new Date(iso).toLocaleDateString('pl-PL', { day: '2-digit', month: '2-digit', year: 'numeric' })
  }

  function openNote(noteId: string) {
    const base = folderPath ? `/workspace/${slug}/notes/${folderPath}` : `/workspace/${slug}/notes`
    goto(`${base}/${noteId}`)
  }
</script>

<div class="notes-list">
  <div class="notes-list__header">
    <span class="notes-list__path">{folderPath || slug}/</span>
    <span class="notes-list__count">{notes.length}</span>
  </div>

  {#if notes.length === 0}
    <p class="notes-list__empty">Brak notatek.</p>
  {:else}
    <ul>
      {#each notes as note}
        <li>
          <button
            class="note-row"
            class:active={note.note_id === currentNoteId}
            onclick={() => openNote(note.note_id)}
          >
            <span class="note-row__title">{note.title}</span>
            <span class="note-row__meta">
              <span class="note-row__size">{formatSize(note.size_bytes)}</span>
              <span class="note-row__date">{formatDate(note.updated_at)}</span>
            </span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .notes-list {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    border-left: 1px solid v.$border;
    border-right: 1px solid v.$border;

    &__header {
      display: flex;
      align-items: center;
      gap: v.$space-sm;
      padding: 8px 12px;
      border-bottom: 1px solid v.$border;
      flex-shrink: 0;
    }

    &__path {
      font-family: v.$font-mono;
      font-size: 0.72rem;
      color: v.$text-muted;
      letter-spacing: 0.03em;
    }

    &__count {
      font-family: v.$font-mono;
      font-size: 0.68rem;
      color: v.$accent-dark;
      background: rgba(240, 184, 0, 0.08);
      border: 1px solid v.$border;
      border-radius: v.$radius-sm;
      padding: 1px 6px;
    }

    &__empty {
      font-family: v.$font-mono;
      font-size: 0.8rem;
      color: v.$text-muted;
      padding: 16px 12px;
    }

    ul {
      list-style: none;
      padding: 0;
      margin: 0;
      overflow-y: auto;
      flex: 1;
    }
  }

  .note-row {
    display: flex;
    flex-direction: column;
    gap: 2px;
    width: 100%;
    padding: 7px 12px;
    background: none;
    border: none;
    border-bottom: 1px solid v.$border;
    cursor: pointer;
    text-align: left;

    &:hover { background: rgba(255,255,255,0.02); }
    &.active { background: rgba(240, 184, 0, 0.06); }

    &__title {
      font-family: v.$font-mono;
      font-size: 0.85rem;
      color: v.$text-primary;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    &__meta {
      display: flex;
      gap: v.$space-sm;
    }

    &__size,
    &__date {
      font-family: v.$font-mono;
      font-size: 0.68rem;
      color: v.$text-muted;
    }
  }
</style>
```

- [ ] **Step 2: Verify types compile**

```bash
cd frontend && npm run check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add "frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/NotesList.svelte"
git commit -m "feat: add NotesList middle panel component"
```

---

## Task 9: `NotePreview.svelte` component

**Files:**
- Create: `frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/NotePreview.svelte`

- [ ] **Step 1: Create `NotePreview.svelte`**

```svelte
<script lang="ts">
  import type { NoteHtmlResponse } from '$lib/api'

  let { note, slug }: {
    note: NoteHtmlResponse | null
    slug: string
  } = $props()
</script>

<div class="preview">
  {#if note}
    <div class="preview__header">
      <span class="preview__path">{note.folder ? note.folder + '/' : ''}{note.title}</span>
      <a
        href="/workspace/{slug}/note/{note.note_id}"
        class="preview__open-link"
        title="Otwórz pełny widok"
      >↗</a>
    </div>
    <div class="preview__body prose">
      <!-- eslint-disable-next-line svelte/no-at-html-tags -->
      {@html note.content_html}
    </div>
  {:else}
    <div class="preview__empty">
      <span>← wybierz notatkę</span>
    </div>
  {/if}
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .preview {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 8px 16px;
      border-bottom: 1px solid v.$border;
      flex-shrink: 0;
    }

    &__path {
      font-family: v.$font-mono;
      font-size: 0.72rem;
      color: v.$text-muted;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    &__open-link {
      font-family: v.$font-mono;
      font-size: 0.8rem;
      color: v.$accent-dark;
      text-decoration: none;
      flex-shrink: 0;
      margin-left: v.$space-sm;
      &:hover { color: v.$accent; }
    }

    &__body {
      padding: 16px;
      overflow-y: auto;
      flex: 1;
      font-size: 0.9rem;
      line-height: 1.6;
      color: v.$text-primary;
    }

    &__empty {
      display: flex;
      align-items: center;
      justify-content: center;
      height: 100%;
      font-family: v.$font-mono;
      font-size: 0.8rem;
      color: v.$text-muted;
    }
  }
</style>
```

- [ ] **Step 2: Verify types compile**

```bash
cd frontend && npm run check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add "frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/NotePreview.svelte"
git commit -m "feat: add NotePreview right panel component"
```

---

## Task 10: Wire up 3-panel explorer in `+page.svelte`

**Files:**
- Create: `frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/+page.svelte`

- [ ] **Step 1: Create `+page.svelte`**

```svelte
<script lang="ts">
  import { page } from '$app/state'
  import FolderTree from './FolderTree.svelte'
  import NotesList from './NotesList.svelte'
  import NotePreview from './NotePreview.svelte'

  let { data } = $props()
  let slug = $derived(page.params.slug)
</script>

<div class="explorer">
  <aside class="explorer__sidebar">
    <FolderTree
      folders={data.tree.folders}
      currentFolder={data.folderPath}
      {slug}
    />
  </aside>

  <section class="explorer__list">
    <NotesList
      notes={data.notes}
      currentNoteId={data.noteId}
      folderPath={data.folderPath}
      {slug}
    />
  </section>

  <section class="explorer__preview">
    <NotePreview note={data.note} {slug} />
  </section>
</div>

<style lang="scss">
  @use '$lib/styles/variables' as v;

  .explorer {
    display: grid;
    grid-template-columns: 200px 280px 1fr;
    height: calc(100vh - 48px); // subtract nav height if any
    overflow: hidden;
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    margin: v.$space-lg;
    background: v.$bg-surface;

    &__sidebar {
      background: v.$bg-base;
      border-right: 1px solid v.$border;
      overflow: hidden;
      padding-top: v.$space-sm;
    }

    &__list {
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }

    &__preview {
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
  }
</style>
```

- [ ] **Step 2: Run type check**

```bash
cd frontend && npm run check
```

Expected: no errors.

- [ ] **Step 3: Start dev server and verify the page loads**

```bash
# Terminal 1: backend
uv run uvicorn kajet_turbo.server:app --reload --port 8000

# Terminal 2: frontend
cd frontend && npm run dev
```

Open `http://localhost:5173`, log in, navigate to a workspace's notes page. Verify:
- 3-panel layout renders
- Sidebar shows workspace name and folders
- Clicking a folder highlights it and loads notes in middle panel
- Clicking a note shows content in right panel
- URL changes correctly (e.g. `/workspace/my-ws/notes/docs/guide/abc1234`)
- Browser back/forward navigates correctly

- [ ] **Step 4: Commit**

```bash
git add "frontend/src/routes/(protected)/workspace/[slug]/notes/[[...path]]/+page.svelte"
git commit -m "feat: wire up 3-panel explorer layout"
```

---

## Done

After Task 10, the workspace explorer is complete. The old `/workspace/[slug]/notes` URL still works (matched by `[[...path]]` with empty path). The `/workspace/[slug]/note/[id]` route (singular) for full-screen note view is unchanged and linked from the `↗` button in `NotePreview`.
