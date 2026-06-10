# Folder Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to create folders from the explorer sidebar using an inline input in the FolderTree panel.

**Architecture:** New `POST /api/workspaces/{name}/folders` endpoint creates a `.gitkeep` file and commits it to git, making the folder persist without notes. The recursive `/ls?recursive=true` endpoint is fixed to scan the filesystem (instead of only the DB) so newly-created empty folders appear in the tree. FolderTree.svelte gains a "+" button and inline input; +page.svelte owns the API call and `invalidateAll()`.

**Tech Stack:** FastAPI + Python (backend), SvelteKit + Svelte 5 + SCSS (frontend), Git (persistence), Orval (TypeScript types generation from OpenAPI)

---

## File Map

| File | Change |
|---|---|
| `src/kajet_turbo/api/schemas.py` | Add `CreateFolderRequest`, `CreateFolderResponse` |
| `src/kajet_turbo/api/workspaces.py` | Add `POST /api/workspaces/{name}/folders`; fix recursive `/ls` |
| `tests/test_api_workspaces.py` | Add tests for folder creation endpoint + recursive ls fix |
| `openapi.json` | Regenerate (run script) |
| `frontend/src/lib/api/index.ts` | Regenerate (run script) |
| `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/FolderTree.svelte` | Add "+" button and inline input |
| `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte` | Add `handleCreateFolder` callback |

---

## Task 1: Backend — schemas, endpoint, recursive ls fix

**Files:**
- Modify: `src/kajet_turbo/api/schemas.py`
- Modify: `src/kajet_turbo/api/workspaces.py`
- Modify: `tests/test_api_workspaces.py`

### Context

The existing `/ls?recursive=true` reads folders from the DB (only folders that have notes). After this task, it will scan the filesystem — so `.gitkeep`-only folders appear in the tree immediately after creation.

The endpoint imports `GitRepository` and `GitError` from `kajet_turbo.repositories.git` (same as other endpoints). Use `ws_service.workspace_path(user["id"], name)` to get the workspace root path.

- [ ] **Step 1: Write failing tests**

Add to `tests/test_api_workspaces.py`:

```python
from pathlib import Path


def test_create_folder_simple(auth_client):
    client, _, ws_path = auth_client
    resp = client.post("/api/workspaces/test-ws/folders", json={"path": "docs"})
    assert resp.status_code == 200
    assert resp.json() == {"path": "docs"}
    assert (Path(ws_path) / "docs" / ".gitkeep").exists()


def test_create_folder_nested(auth_client):
    client, _, ws_path = auth_client
    resp = client.post("/api/workspaces/test-ws/folders", json={"path": "a/b/c"})
    assert resp.status_code == 200
    assert (Path(ws_path) / "a" / "b" / "c" / ".gitkeep").exists()


def test_create_folder_idempotent(auth_client):
    client, _, ws_path = auth_client
    client.post("/api/workspaces/test-ws/folders", json={"path": "docs"})
    resp = client.post("/api/workspaces/test-ws/folders", json={"path": "docs"})
    assert resp.status_code == 200
    assert resp.json() == {"path": "docs"}


def test_create_folder_path_traversal(auth_client):
    client, _, _ = auth_client
    resp = client.post("/api/workspaces/test-ws/folders", json={"path": "../evil"})
    assert resp.status_code == 422


def test_create_folder_empty_segment(auth_client):
    client, _, _ = auth_client
    resp = client.post("/api/workspaces/test-ws/folders", json={"path": "a//b"})
    assert resp.status_code == 422


def test_create_folder_invalid_chars(auth_client):
    client, _, _ = auth_client
    resp = client.post("/api/workspaces/test-ws/folders", json={"path": "my folder?"})
    assert resp.status_code == 422


def test_create_folder_dot_segment(auth_client):
    client, _, _ = auth_client
    resp = client.post("/api/workspaces/test-ws/folders", json={"path": "."})
    assert resp.status_code == 422


def test_create_folder_returns_401_when_anon(anon_client):
    resp = anon_client.post("/api/workspaces/test-ws/folders", json={"path": "docs"})
    assert resp.status_code == 401


def test_create_folder_returns_403_when_no_access(no_access_client):
    resp = no_access_client.post("/api/workspaces/test-ws/folders", json={"path": "docs"})
    assert resp.status_code == 403


def test_ls_recursive_includes_empty_folder(auth_client):
    client, _, ws_path = auth_client
    # create folder with only .gitkeep (no notes)
    gitkeep = Path(ws_path) / "empty-dir" / ".gitkeep"
    gitkeep.parent.mkdir(parents=True)
    gitkeep.touch()

    resp = client.get("/api/workspaces/test-ws/ls?recursive=true")
    assert resp.status_code == 200
    assert "empty-dir" in resp.json()["folders"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_api_workspaces.py::test_create_folder_simple tests/test_api_workspaces.py::test_ls_recursive_includes_empty_folder -v
```

Expected: `FAILED` — `405 Method Not Allowed` and `AssertionError`.

- [ ] **Step 3: Add schemas to `src/kajet_turbo/api/schemas.py`**

Append to the end of the file:

```python
class CreateFolderRequest(BaseModel):
    path: str


class CreateFolderResponse(BaseModel):
    path: str
```

- [ ] **Step 4: Add endpoint and fix recursive ls in `src/kajet_turbo/api/workspaces.py`**

Add `import re` at the top of the file (after existing imports):

```python
import re
```

Add the regex constant after the existing `_ALLOWED_*` constants at the top of the file:

```python
_FOLDER_PATH_RE = re.compile(r'^[a-zA-Z0-9._-][a-zA-Z0-9._\-/]*$')
```

Update the imports from `kajet_turbo.api.schemas` to include the new schemas:

```python
from kajet_turbo.api.schemas import (
    CreateFolderRequest, CreateFolderResponse,
    CreateWorkspaceResponse, LsEntry, LsResponse,
    NoteHistoryResponse, NoteHtmlResponse, NoteMarkdownResponse,
    NotesListResponse, RestoreVersionResponse, WorkspacesListResponse,
)
```

Fix the recursive `/ls` case — replace lines 135–142 in `api_ls`:

```python
    if recursive:
        expanded: set[str] = set()
        for dirpath in ws_root.rglob("*"):
            if not dirpath.is_dir():
                continue
            rel_parts = dirpath.relative_to(ws_root).parts
            if any(p.startswith(".") for p in rel_parts):
                continue
            expanded.add("/".join(rel_parts))
        return JSONResponse({"folders": sorted(expanded), "entries": []})
```

Add the new endpoint after `api_ls` (before `api_get_note_html`):

```python
@router.post("/api/workspaces/{name}/folders", response_model=CreateFolderResponse)
@logged_route
async def api_create_folder(
    name: str,
    request: Request,
    ws_service: WorkspaceService = Depends(get_workspace_service),
) -> JSONResponse:
    from kajet_turbo.repositories.git import GitRepository, GitError
    user = get_session_user(request)
    if not user:
        return JSONResponse({"error": "Not logged in"}, status_code=401)
    if not ws_service.has_access(user["id"], name):
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    path = str(body.get("path", "")).strip().strip("/")
    if not path:
        return JSONResponse({"error": "Ścieżka jest wymagana."}, status_code=422)
    segments = path.split("/")
    if any(not s or s in (".", "..") for s in segments):
        return JSONResponse({"error": "Niedozwolona ścieżka."}, status_code=422)
    if not _FOLDER_PATH_RE.match(path):
        return JSONResponse({"error": "Niedozwolone znaki w ścieżce."}, status_code=422)
    ws_path = ws_service.workspace_path(user["id"], name)
    ws_root = Path(ws_path).resolve()
    target = (ws_root / path).resolve()
    try:
        target.relative_to(ws_root)
    except ValueError:
        return JSONResponse({"error": "Niedozwolona ścieżka."}, status_code=422)
    gitkeep = target / ".gitkeep"
    gitkeep.parent.mkdir(parents=True, exist_ok=True)
    if not gitkeep.exists():
        gitkeep.touch()
        relative = str(gitkeep.relative_to(ws_root))
        try:
            GitRepository(ws_path).commit_file(relative, f"folder: add {path}")
        except GitError as e:
            gitkeep.unlink(missing_ok=True)
            return JSONResponse({"error": str(e)}, status_code=500)
    logger.info("folder_created", ws=name, path=path)
    return JSONResponse({"path": path})
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_api_workspaces.py::test_create_folder_simple tests/test_api_workspaces.py::test_create_folder_nested tests/test_api_workspaces.py::test_create_folder_idempotent tests/test_api_workspaces.py::test_create_folder_path_traversal tests/test_api_workspaces.py::test_create_folder_empty_segment tests/test_api_workspaces.py::test_create_folder_invalid_chars tests/test_api_workspaces.py::test_create_folder_dot_segment tests/test_api_workspaces.py::test_create_folder_returns_401_when_anon tests/test_api_workspaces.py::test_create_folder_returns_403_when_no_access tests/test_api_workspaces.py::test_ls_recursive_includes_empty_folder -v
```

Expected: all `PASSED`.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
uv run pytest -v
```

Expected: all tests pass. Note: `test_export_openapi` will regenerate `openapi.json` as a side effect — that is expected.

- [ ] **Step 7: Commit**

```bash
git add src/kajet_turbo/api/schemas.py src/kajet_turbo/api/workspaces.py tests/test_api_workspaces.py
git commit -m "feat: add POST /folders endpoint, fix recursive ls to include empty dirs"
```

---

## Task 2: Regenerate TypeScript API types

**Files:**
- Modify: `openapi.json`
- Modify: `frontend/src/lib/api/index.ts`

After the backend changes, the OpenAPI schema has a new endpoint. Regenerate the TypeScript client so types are available on the frontend.

- [ ] **Step 1: Run generation script**

```bash
cd /path/to/project && bash scripts/generate-api.sh
```

Expected output:
```
→ Exporting OpenAPI schema...
→ Generating TypeScript client with Orval...
✓ Done — client generated in frontend/src/lib/api/
```

- [ ] **Step 2: Verify the new types exist**

```bash
grep "CreateFolder" frontend/src/lib/api/index.ts
```

Expected: lines containing `CreateFolderRequest` and `CreateFolderResponse`.

- [ ] **Step 3: Commit**

```bash
git add openapi.json frontend/src/lib/api/index.ts
git commit -m "chore: regenerate Orval types for folder creation endpoint"
```

---

## Task 3: Frontend — `+page.svelte` create folder handler

**Files:**
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte`

The page owns the API call and the navigation after creation. `FolderTree` receives `onCreateFolder` as a callback prop and calls it — the page handles the HTTP request and re-fetching.

- [ ] **Step 1: Replace the contents of `+page.svelte`**

```svelte
<script lang="ts">
  import { invalidateAll, goto } from '$app/navigation'
  import FolderTree from './FolderTree.svelte'
  import NotesList from './NotesList.svelte'
  import NotePreview from './NotePreview.svelte'

  let { data } = $props()
  let slug = data.slug

  async function handleCreateFolder(path: string): Promise<void> {
    const resp = await fetch(`/api/workspaces/${slug}/folders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ path }),
    })
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}))
      throw new Error(body.error ?? 'Nie udało się utworzyć folderu')
    }
    await invalidateAll()
    goto(`/workspace/${slug}/notes/${path}`)
  }
</script>

<div class="explorer">
  <aside class="explorer__sidebar">
    <FolderTree
      folders={data.tree.folders}
      currentFolder={data.folderPath}
      {slug}
      onCreateFolder={handleCreateFolder}
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
    height: calc(100vh - 48px);
    overflow: hidden;
    border: 1px solid v.$border;
    border-radius: v.$radius-lg;
    margin: v.$space-lg;
    background: v.$bg-surface;

    &__sidebar {
      background: v.$bg-deep;
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

- [ ] **Step 2: Commit**

```bash
git add "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte"
git commit -m "feat: add handleCreateFolder in explorer page"
```

---

## Task 4: Frontend — `FolderTree.svelte` "+" button and inline input

**Files:**
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/FolderTree.svelte`

The component adds:
- A "+" button next to the workspace root label
- `creatingIn` state tracking which folder we're creating inside
- An inline input that renders at the correct tree level
- Keyboard handling: `Enter` submits, `Escape` cancels

- [ ] **Step 1: Replace the contents of `FolderTree.svelte`**

```svelte
<script lang="ts">
  import { goto } from '$app/navigation'

  let { folders, currentFolder, slug, onCreateFolder }: {
    folders: string[]
    currentFolder: string
    slug: string
    onCreateFolder: (path: string) => Promise<void>
  } = $props()

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
  let expandedOverride = $state<Set<string> | null>(null)
  let expanded = $derived(
    expandedOverride ?? new Set<string>(
      currentFolder
        ? currentFolder.split('/').map((_, i, arr) => arr.slice(0, i + 1).join('/'))
        : []
    )
  )

  function toggle(path: string) {
    const next = new Set(expanded)
    next.has(path) ? next.delete(path) : next.add(path)
    expandedOverride = next
  }

  function navigate(folder: string) {
    goto(`/workspace/${slug}/notes/${folder}`)
  }

  let creatingIn: string | null = $state(null)
  let newFolderInput = $state('')
  let createError = $state('')

  function startCreating() {
    creatingIn = currentFolder
    newFolderInput = ''
    createError = ''
    // ensure current folder is expanded so inline input is visible
    if (currentFolder) {
      const next = new Set(expanded)
      currentFolder.split('/').forEach((_, i, arr) => next.add(arr.slice(0, i + 1).join('/')))
      expandedOverride = next
    }
  }

  async function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      creatingIn = null
      return
    }
    if (e.key !== 'Enter') return
    const name = newFolderInput.trim()
    if (!name) return
    if (!/^[a-zA-Z0-9._-][a-zA-Z0-9._\-/]*$/.test(name)) {
      createError = 'Tylko litery, cyfry, kropka, myślnik, ukośnik'
      return
    }
    const fullPath = creatingIn ? `${creatingIn}/${name}` : name
    try {
      await onCreateFolder(fullPath)
      creatingIn = null
    } catch (err: unknown) {
      createError = err instanceof Error ? err.message : 'Błąd'
    }
  }
</script>

{#snippet inlineInput(parentPath: string)}
  {#if creatingIn === parentPath}
    <li class="new-folder-row">
      <span class="folder-chevron"></span>
      <input
        class="new-folder-input"
        class:new-folder-input--error={!!createError}
        bind:value={newFolderInput}
        onkeydown={handleKeydown}
        placeholder="nazwa-folderu"
        autofocus
      />
    </li>
    {#if createError}
      <li class="new-folder-error">{createError}</li>
    {/if}
  {/if}
{/snippet}

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
    {#if expanded.has(n.fullPath)}
      <ul class="subtree">
        {#each n.children as child}
          {@render node(child)}
        {/each}
        {@render inlineInput(n.fullPath)}
      </ul>
    {/if}
  </li>
{/snippet}

<nav class="folder-tree">
  <div class="tree-header">
    <button
      class="folder-row root-row"
      class:active={currentFolder === ''}
      onclick={() => navigate('')}
    >
      <span class="folder-name">{slug}</span>
    </button>
    <button class="create-btn" onclick={startCreating} title="Nowy folder">+</button>
  </div>
  <ul class="tree-root">
    {#each tree as n}
      {@render node(n)}
    {/each}
    {@render inlineInput('')}
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

  .tree-header {
    display: flex;
    align-items: center;
    padding-right: 8px;
    margin-bottom: 4px;
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
    flex: 1;
    padding: 4px 12px;
    font-size: 0.75rem;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: v.$text-muted;
  }

  .folder-chevron {
    font-size: 0.6rem;
    width: 10px;
    flex-shrink: 0;
  }

  .create-btn {
    background: none;
    border: none;
    color: v.$accent;
    font-size: 1.1rem;
    line-height: 1;
    padding: 0 6px;
    cursor: pointer;
    flex-shrink: 0;

    &:hover { color: v.$accent-hover; }
  }

  .new-folder-row {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 3px 12px;
  }

  .new-folder-input {
    background: v.$bg-raised;
    border: 1px solid v.$accent;
    color: v.$text-primary;
    font-family: v.$font-mono;
    font-size: 0.82rem;
    padding: 1px 5px;
    outline: none;
    border-radius: v.$radius-sm;
    width: 120px;

    &--error { border-color: v.$error; }
  }

  .new-folder-error {
    font-family: v.$font-mono;
    font-size: 0.72rem;
    color: v.$error;
    padding: 1px 12px 3px 22px;
  }
</style>
```

- [ ] **Step 2: Commit**

```bash
git add "frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/FolderTree.svelte"
git commit -m "feat: add inline folder creation to FolderTree"
```

---

## Task 5: Smoke test in the browser

- [ ] **Step 1: Start dev server**

```bash
# Terminal 1 — backend
uv run uvicorn kajet_turbo.app:app --reload

# Terminal 2 — frontend
cd frontend && bun dev
```

- [ ] **Step 2: Manual test flow**

1. Otwórz explorer dowolnego workspace
2. Kliknij "+" przy nazwie workspace — powinien pojawić się inline input
3. Wpisz `test-folder` i naciśnij `Enter` — folder powinien pojawić się w drzewie, URL powinien zmienić się na `.../notes/test-folder`
4. Wróć do roota, wpisz `a/b/c` — sprawdź że zagnieżdżona ścieżka działa
5. Wpisz `test-folder` ponownie (idempotentność) — brak błędu
6. Naciśnij `Escape` w trakcie wpisywania — input znika
7. Wejdź do folderu `a`, kliknij "+" — wpisz `child` — nowy folder powinien powstać jako `a/child`
