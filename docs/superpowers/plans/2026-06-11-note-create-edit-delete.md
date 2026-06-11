# Note Create / Edit / Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add REST endpoints and frontend UI to create, edit, and delete markdown notes directly from the browser.

**Architecture:** Three new REST endpoints (`POST/PATCH/DELETE /api/workspaces/{name}/notes[/{id}]`) delegate to the existing `NoteService` methods. The frontend adds an inline note-title input to `NotesList` (mirrors folder-creation UX), a new dedicated edit page at `/workspace/[slug]/note/[id]/edit`, and an "Edytuj" button on the note view page.

**Tech Stack:** FastAPI + Python (backend), SvelteKit 2 + Svelte 5 runes (frontend), `uv run pytest` for tests, `bash scripts/generate-api.sh` for Orval type regeneration.

---

## File map

**Create / modify:**
- `src/kajet_turbo/api/schemas.py` — add 5 new schema classes
- `src/kajet_turbo/api/workspaces.py` — add 3 new route handlers
- `tests/test_api_workspaces.py` — add tests for all 3 endpoints
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/NotesList.svelte` — inline create UI
- `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte` — `handleCreateNote` callback
- `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/+page.svelte` — Edytuj button

**Create new:**
- `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/edit/+page.ts` — load markdown for edit
- `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/edit/+page.svelte` — edit form

---

## Task 1: Add new Pydantic schemas

**Files:**
- Modify: `src/kajet_turbo/api/schemas.py`

- [ ] **Step 1: Add the 5 new schema classes to `schemas.py`**

  Append at the end of the file:

  ```python
  class CreateNoteRequest(BaseModel):
      title: str
      content: str = ""
      folder: str = ""
      tags: list[str] = []


  class CreateNoteResponse(BaseModel):
      note_id: str


  class UpdateNoteRequest(BaseModel):
      title: str | None = None
      content: str | None = None
      folder: str | None = None
      tags: list[str] | None = None


  class UpdateNoteResponse(BaseModel):
      note_id: str


  class DeleteNoteResponse(BaseModel):
      ok: bool
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/kajet_turbo/api/schemas.py
  git commit -m "feat: add CreateNote/UpdateNote/DeleteNote Pydantic schemas"
  ```

---

## Task 2: POST /api/workspaces/{name}/notes endpoint

**Files:**
- Modify: `src/kajet_turbo/api/workspaces.py`
- Test: `tests/test_api_workspaces.py`

- [ ] **Step 1: Write the failing tests**

  Add to `tests/test_api_workspaces.py`:

  ```python
  def test_create_note_returns_note_id(auth_client):
      client, _, _ = auth_client
      resp = client.post(
          "/api/workspaces/test-ws/notes",
          json={"title": "Nowa Notatka", "content": "treść", "folder": ""},
      )
      assert resp.status_code == 201
      data = resp.json()
      assert "note_id" in data
      assert len(data["note_id"]) > 0


  def test_create_note_in_subfolder(auth_client):
      client, note_svc, ws_path = auth_client
      resp = client.post(
          "/api/workspaces/test-ws/notes",
          json={"title": "Subfolder Note", "content": "", "folder": "docs"},
      )
      assert resp.status_code == 201
      note_id = resp.json()["note_id"]
      note = note_svc.get_with_content(note_id, owner_id="u1", ws_path=ws_path)
      assert note is not None
      assert note["folder"] == "docs"


  def test_create_note_duplicate_returns_409(auth_client):
      client, _, _ = auth_client
      client.post("/api/workspaces/test-ws/notes", json={"title": "Dup"})
      resp = client.post("/api/workspaces/test-ws/notes", json={"title": "Dup"})
      assert resp.status_code == 409
      assert "error" in resp.json()


  def test_create_note_missing_title_returns_422(auth_client):
      client, _, _ = auth_client
      resp = client.post("/api/workspaces/test-ws/notes", json={"content": "x"})
      assert resp.status_code == 422


  def test_create_note_returns_401_when_anon(anon_client):
      resp = anon_client.post("/api/workspaces/test-ws/notes", json={"title": "T"})
      assert resp.status_code == 401


  def test_create_note_returns_403_when_no_access(no_access_client):
      resp = no_access_client.post("/api/workspaces/test-ws/notes", json={"title": "T"})
      assert resp.status_code == 403
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest tests/test_api_workspaces.py -k "create_note" -v
  ```

  Expected: `FAILED` — `404 Not Found` (route doesn't exist yet).

- [ ] **Step 3: Add the import to `workspaces.py`**

  Find the existing schema import block (near the top of `workspaces.py`):

  ```python
  from kajet_turbo.api.schemas import (
      CreateFolderRequest, CreateFolderResponse,
      CreateWorkspaceResponse, LsEntry, LsResponse,
      NoteHistoryResponse, NoteHtmlResponse, NoteMarkdownResponse,
      NotesListResponse, RestoreVersionResponse, WorkspacesListResponse,
  )
  ```

  Replace with:

  ```python
  from kajet_turbo.api.schemas import (
      CreateFolderRequest, CreateFolderResponse,
      CreateNoteRequest, CreateNoteResponse,
      CreateWorkspaceResponse, LsEntry, LsResponse,
      NoteHistoryResponse, NoteHtmlResponse, NoteMarkdownResponse,
      NotesListResponse, RestoreVersionResponse,
      UpdateNoteRequest, UpdateNoteResponse,
      DeleteNoteResponse,
      WorkspacesListResponse,
  )
  ```

- [ ] **Step 4: Add the POST /notes handler to `workspaces.py`**

  Add after the `api_create_folder` handler:

  ```python
  @router.post("/api/workspaces/{name}/notes", status_code=201, response_model=CreateNoteResponse)
  @logged_route
  async def api_create_note(
      name: str,
      request: Request,
      ws_service: WorkspaceService = Depends(get_workspace_service),
      note_service: NoteService = Depends(get_note_service),
  ) -> JSONResponse:
      user = get_session_user(request)
      if not user:
          return JSONResponse({"error": "Not logged in"}, status_code=401)
      if not ws_service.has_access(user["id"], name):
          return JSONResponse({"error": "Brak dostępu."}, status_code=403)
      try:
          body = await request.json()
      except Exception:
          return JSONResponse({"error": "Invalid JSON"}, status_code=400)
      title = str(body.get("title", "")).strip()
      if not title:
          return JSONResponse({"error": "Tytuł jest wymagany."}, status_code=422)
      content = str(body.get("content", ""))
      folder = str(body.get("folder", ""))
      tags = body.get("tags", [])
      if not isinstance(tags, list):
          tags = []
      ws_path = ws_service.workspace_path(user["id"], name)
      try:
          result = note_service.save(user["id"], name, ws_path, title, content, tags, folder=folder)
      except ValueError as e:
          return JSONResponse({"error": str(e)}, status_code=409)
      except Exception as e:
          return JSONResponse({"error": str(e)}, status_code=500)
      return JSONResponse(result, status_code=201)
  ```

- [ ] **Step 5: Run tests to verify they pass**

  ```bash
  uv run pytest tests/test_api_workspaces.py -k "create_note" -v
  ```

  Expected: all 6 tests `PASSED`.

- [ ] **Step 6: Run the full test suite to check for regressions**

  ```bash
  uv run pytest -v
  ```

  Expected: all tests `PASSED`.

- [ ] **Step 7: Commit**

  ```bash
  git add src/kajet_turbo/api/workspaces.py tests/test_api_workspaces.py
  git commit -m "feat: add POST /api/workspaces/{name}/notes endpoint"
  ```

---

## Task 3: PATCH /api/workspaces/{name}/notes/{note_id} endpoint

**Files:**
- Modify: `src/kajet_turbo/api/workspaces.py`
- Test: `tests/test_api_workspaces.py`

- [ ] **Step 1: Write the failing tests**

  Add to `tests/test_api_workspaces.py`:

  ```python
  def test_update_note_content(auth_client):
      client, note_svc, ws_path = auth_client
      note_id = note_svc.save("u1", "test-ws", ws_path, "Orig", "old content", [])["note_id"]
      resp = client.patch(
          f"/api/workspaces/test-ws/notes/{note_id}",
          json={"content": "new content"},
      )
      assert resp.status_code == 200
      assert resp.json()["note_id"] == note_id
      updated = note_svc.get_with_content(note_id, owner_id="u1", ws_path=ws_path)
      assert updated["content"] == "new content"


  def test_update_note_title(auth_client):
      client, note_svc, ws_path = auth_client
      note_id = note_svc.save("u1", "test-ws", ws_path, "Old Title", "c", [])["note_id"]
      resp = client.patch(
          f"/api/workspaces/test-ws/notes/{note_id}",
          json={"title": "New Title"},
      )
      assert resp.status_code == 200
      updated = note_svc.get(note_id, owner_id="u1")
      assert updated["title"] == "New Title"


  def test_update_note_not_found_returns_404(auth_client):
      client, _, _ = auth_client
      resp = client.patch(
          "/api/workspaces/test-ws/notes/nonexistent",
          json={"content": "x"},
      )
      assert resp.status_code == 404


  def test_update_note_returns_401_when_anon(anon_client):
      resp = anon_client.patch("/api/workspaces/test-ws/notes/abc", json={"content": "x"})
      assert resp.status_code == 401


  def test_update_note_returns_403_when_no_access(no_access_client):
      resp = no_access_client.patch("/api/workspaces/test-ws/notes/abc", json={"content": "x"})
      assert resp.status_code == 403
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest tests/test_api_workspaces.py -k "update_note" -v
  ```

  Expected: `FAILED` — `405 Method Not Allowed`.

- [ ] **Step 3: Add the PATCH handler to `workspaces.py`**

  Add after the `api_create_note` handler:

  ```python
  @router.patch("/api/workspaces/{name}/notes/{note_id}", response_model=UpdateNoteResponse)
  @logged_route
  async def api_update_note(
      name: str,
      note_id: str,
      request: Request,
      ws_service: WorkspaceService = Depends(get_workspace_service),
      note_service: NoteService = Depends(get_note_service),
  ) -> JSONResponse:
      user = get_session_user(request)
      if not user:
          return JSONResponse({"error": "Not logged in"}, status_code=401)
      if not ws_service.has_access(user["id"], name):
          return JSONResponse({"error": "Brak dostępu."}, status_code=403)
      try:
          body = await request.json()
      except Exception:
          return JSONResponse({"error": "Invalid JSON"}, status_code=400)
      title = body.get("title")
      content = body.get("content")
      tags = body.get("tags")
      folder = body.get("folder")
      ws_path = ws_service.workspace_path(user["id"], name)
      try:
          result = note_service.update(
              note_id,
              owner_id=user["id"],
              ws_path=ws_path,
              title=title,
              content=content,
              tags=tags,
              folder=folder,
          )
      except (ValueError, FileNotFoundError) as e:
          return JSONResponse({"error": str(e)}, status_code=404)
      return JSONResponse(result)
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  uv run pytest tests/test_api_workspaces.py -k "update_note" -v
  ```

  Expected: all 5 tests `PASSED`.

- [ ] **Step 5: Run full suite**

  ```bash
  uv run pytest -v
  ```

  Expected: all tests `PASSED`.

- [ ] **Step 6: Commit**

  ```bash
  git add src/kajet_turbo/api/workspaces.py tests/test_api_workspaces.py
  git commit -m "feat: add PATCH /api/workspaces/{name}/notes/{note_id} endpoint"
  ```

---

## Task 4: DELETE /api/workspaces/{name}/notes/{note_id} endpoint

**Files:**
- Modify: `src/kajet_turbo/api/workspaces.py`
- Test: `tests/test_api_workspaces.py`

- [ ] **Step 1: Write the failing tests**

  Add to `tests/test_api_workspaces.py`:

  ```python
  def test_delete_note_removes_it(auth_client):
      client, note_svc, ws_path = auth_client
      note_id = note_svc.save("u1", "test-ws", ws_path, "To Delete", "c", [])["note_id"]
      resp = client.delete(f"/api/workspaces/test-ws/notes/{note_id}")
      assert resp.status_code == 200
      assert resp.json() == {"ok": True}
      assert note_svc.get(note_id, owner_id="u1") is None


  def test_delete_note_not_found_returns_404(auth_client):
      client, _, _ = auth_client
      resp = client.delete("/api/workspaces/test-ws/notes/nonexistent")
      assert resp.status_code == 404


  def test_delete_note_returns_401_when_anon(anon_client):
      resp = anon_client.delete("/api/workspaces/test-ws/notes/abc")
      assert resp.status_code == 401


  def test_delete_note_returns_403_when_no_access(no_access_client):
      resp = no_access_client.delete("/api/workspaces/test-ws/notes/abc")
      assert resp.status_code == 403
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  uv run pytest tests/test_api_workspaces.py -k "delete_note" -v
  ```

  Expected: `FAILED` — `405 Method Not Allowed`.

- [ ] **Step 3: Add the DELETE handler to `workspaces.py`**

  Add after the `api_update_note` handler:

  ```python
  @router.delete("/api/workspaces/{name}/notes/{note_id}", response_model=DeleteNoteResponse)
  @logged_route
  async def api_delete_note(
      name: str,
      note_id: str,
      request: Request,
      ws_service: WorkspaceService = Depends(get_workspace_service),
      note_service: NoteService = Depends(get_note_service),
  ) -> JSONResponse:
      user = get_session_user(request)
      if not user:
          return JSONResponse({"error": "Not logged in"}, status_code=401)
      if not ws_service.has_access(user["id"], name):
          return JSONResponse({"error": "Brak dostępu."}, status_code=403)
      ws_path = ws_service.workspace_path(user["id"], name)
      try:
          note_service.delete(note_id, owner_id=user["id"], ws_path=ws_path)
      except ValueError as e:
          return JSONResponse({"error": str(e)}, status_code=404)
      return JSONResponse({"ok": True})
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  uv run pytest tests/test_api_workspaces.py -k "delete_note" -v
  ```

  Expected: all 4 tests `PASSED`.

- [ ] **Step 5: Run full suite**

  ```bash
  uv run pytest -v
  ```

  Expected: all tests `PASSED`.

- [ ] **Step 6: Commit**

  ```bash
  git add src/kajet_turbo/api/workspaces.py tests/test_api_workspaces.py
  git commit -m "feat: add DELETE /api/workspaces/{name}/notes/{note_id} endpoint"
  ```

---

## Task 5: Regenerate Orval TypeScript types

**Files:**
- Modified by script: `frontend/src/lib/api/index.ts`
- Modified by script: `openapi.json`

- [ ] **Step 1: Run the generate-api script**

  ```bash
  bash scripts/generate-api.sh
  ```

  Expected output ends with: `✓ Done — client generated in frontend/src/lib/api/`

- [ ] **Step 2: Verify the new functions exist in the generated file**

  ```bash
  grep "apiCreateNote\|apiUpdateNote\|apiDeleteNote" frontend/src/lib/api/index.ts
  ```

  Expected: three lines containing:
  - `apiCreateNoteApiWorkspacesNameNotesPost`
  - `apiUpdateNoteApiWorkspacesNameNotesNoteIdPatch`
  - `apiDeleteNoteApiWorkspacesNameNotesNoteIdDelete`

- [ ] **Step 3: Commit**

  ```bash
  git add openapi.json frontend/src/lib/api/index.ts
  git commit -m "chore: regenerate Orval types for note CRUD endpoints"
  ```

---

## Task 6: Inline note creation in NotesList

**Files:**
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/NotesList.svelte`
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/notes/[...path]/+page.svelte`

- [ ] **Step 1: Update `NotesList.svelte` — add `onCreateNote` prop and inline state**

  Replace the entire `<script>` block:

  ```svelte
  <script lang="ts">
    import { goto } from '$app/navigation'
    import type { NoteItem } from '$lib/api'

    let { notes, currentNoteId, folderPath, slug, onCreateNote }: {
      notes: NoteItem[]
      currentNoteId: string | null
      folderPath: string
      slug: string
      onCreateNote: (title: string) => Promise<void>
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

    let creating = $state(false)
    let newNoteTitle = $state('')
    let createError = $state('')

    function startCreating() {
      creating = true
      newNoteTitle = ''
      createError = ''
    }

    async function handleKeydown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        creating = false
        newNoteTitle = ''
        createError = ''
        return
      }
      if (e.key !== 'Enter') return
      const title = newNoteTitle.trim()
      if (!title) return
      try {
        await onCreateNote(title)
        creating = false
        newNoteTitle = ''
        createError = ''
      } catch (err: unknown) {
        createError = err instanceof Error ? err.message : 'Błąd'
      }
    }
  </script>
  ```

- [ ] **Step 2: Update `NotesList.svelte` — add `+` button and inline input to the template**

  Replace the `<div class="notes-list__header">` section:

  ```svelte
  <div class="notes-list__header">
    <span class="notes-list__path">{folderPath || slug}/</span>
    <span class="notes-list__count">{notes.length}</span>
    <button class="create-btn" onclick={startCreating} title="Nowa notatka">+</button>
  </div>
  ```

  Add the inline input after the header and before the `{#if notes.length === 0}` block:

  ```svelte
  {#if creating}
    <div class="new-note-row">
      <input
        class="new-note-input"
        class:new-note-input--error={!!createError}
        bind:value={newNoteTitle}
        onkeydown={handleKeydown}
        placeholder="tytuł-notatki"
        autofocus
      />
      {#if createError}
        <span class="new-note-error">{createError}</span>
      {/if}
    </div>
  {/if}
  ```

- [ ] **Step 3: Add styles for the new elements to `NotesList.svelte`**

  Add inside the `<style lang="scss">` block, after the existing `.notes-list` rules:

  ```scss
  .create-btn {
    margin-left: auto;
    background: none;
    border: none;
    color: v.$text-muted;
    font-family: v.$font-mono;
    font-size: 1rem;
    cursor: pointer;
    padding: 0 2px;
    line-height: 1;
    transition: color 0.15s;

    &:hover { color: v.$accent; }
  }

  .new-note-row {
    padding: 6px 12px;
    border-bottom: 1px solid v.$border;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .new-note-input {
    width: 100%;
    background: v.$bg-raised;
    border: 1px solid v.$border;
    border-radius: v.$radius-sm;
    color: v.$text-primary;
    font-family: v.$font-mono;
    font-size: 0.82rem;
    padding: 4px 8px;
    outline: none;
    box-sizing: border-box;

    &:focus { border-color: v.$accent-dark; }
    &--error { border-color: #c0392b; }
  }

  .new-note-error {
    font-family: v.$font-mono;
    font-size: 0.72rem;
    color: #c0392b;
  }
  ```

- [ ] **Step 4: Add `handleCreateNote` to `+page.svelte`**

  Add the following function inside the `<script>` block of `+page.svelte` (after `handleCreateFolder`):

  ```typescript
  async function handleCreateNote(title: string): Promise<void> {
    const resp = await fetch(`/api/workspaces/${slug}/notes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ title, folder: data.folderPath, content: '' }),
    })
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}))
      throw new Error(body.error ?? 'Nie udało się utworzyć notatki')
    }
    const { note_id } = await resp.json()
    await invalidate('app:workspace-tree')
    goto(`/workspace/${slug}/note/${note_id}/edit`)
  }
  ```

- [ ] **Step 5: Pass `onCreateNote` prop to `NotesList` in `+page.svelte`**

  Find:
  ```svelte
  <NotesList
    notes={data.notes}
    currentNoteId={data.noteId}
    folderPath={data.folderPath}
    {slug}
  />
  ```

  Replace with:
  ```svelte
  <NotesList
    notes={data.notes}
    currentNoteId={data.noteId}
    folderPath={data.folderPath}
    {slug}
    onCreateNote={handleCreateNote}
  />
  ```

- [ ] **Step 6: Start the dev server and verify inline note creation works**

  ```bash
  cd frontend && bun run dev
  ```

  Open the explorer in a browser. Click `+` in the notes list header. Type a title, press Enter. Should navigate to `/workspace/{slug}/note/{id}/edit` (edit page will 404 until Task 7). Press Escape — input should close without creating.

- [ ] **Step 7: Commit**

  ```bash
  git add frontend/src/routes/\(protected\)/workspace/\[slug\]/notes/\[...path\]/NotesList.svelte
  git add frontend/src/routes/\(protected\)/workspace/\[slug\]/notes/\[...path\]/+page.svelte
  git commit -m "feat: add inline note creation to NotesList"
  ```

---

## Task 7: Edit page `/workspace/[slug]/note/[id]/edit`

**Files:**
- Create: `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/edit/+page.ts`
- Create: `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/edit/+page.svelte`

- [ ] **Step 1: Create `+page.ts`**

  Create file `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/edit/+page.ts`:

  ```typescript
  import { error, redirect } from '@sveltejs/kit'
  import { apiGetNoteMarkdownApiWorkspacesNameNotesNoteIdMarkdownGet } from '$lib/api'
  import type { PageLoad } from './$types'

  export const load: PageLoad = async ({ params }) => {
    const result = await apiGetNoteMarkdownApiWorkspacesNameNotesNoteIdMarkdownGet(
      params.slug,
      params.id,
      { credentials: 'include' },
    ).catch(() => null)

    const status = result?.status as number | undefined
    if (status === 401) redirect(307, '/login')
    if (status === 403 || status === 404) error(404, 'Notatka nie istnieje.')
    if (!result || status !== 200) error(500, 'Błąd serwera.')

    return { note: result.data as any, slug: params.slug }
  }
  ```

- [ ] **Step 2: Create `+page.svelte`**

  Create file `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/edit/+page.svelte`:

  ```svelte
  <script lang="ts">
    import { goto } from '$app/navigation'

    let { data } = $props()
    let note = $derived(data.note)
    let slug = $derived(data.slug)

    let title = $state(note.title as string)
    let content = $state((note.content as string) ?? '')
    let saveError = $state('')
    let saving = $state(false)

    async function handleSave() {
      saving = true
      saveError = ''
      try {
        const resp = await fetch(`/api/workspaces/${slug}/notes/${note.note_id}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ title: title.trim(), content }),
        })
        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}))
          saveError = body.error ?? 'Nie udało się zapisać'
          return
        }
        goto(`/workspace/${slug}/note/${note.note_id}`)
      } finally {
        saving = false
      }
    }

    async function handleDelete() {
      if (!window.confirm(`Usunąć notatkę "${note.title}"?`)) return
      const resp = await fetch(`/api/workspaces/${slug}/notes/${note.note_id}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        saveError = body.error ?? 'Nie udało się usunąć'
        return
      }
      const folderPath = note.folder ?? ''
      goto(folderPath ? `/workspace/${slug}/notes/${folderPath}` : `/workspace/${slug}/notes`)
    }

    function handleCancel() {
      goto(`/workspace/${slug}/note/${note.note_id}`)
    }
  </script>

  <main class="page">
    <nav class="breadcrumb">
      <a href="/workspaces" class="breadcrumb__link">Workspaces</a>
      <span class="breadcrumb__sep">/</span>
      <a href="/workspace/{slug}/notes" class="breadcrumb__link">{slug}</a>
      {#if note.folder}
        {#each note.folder.split('/') as segment}
          <span class="breadcrumb__sep">/</span>
          <span class="breadcrumb__folder">{segment}</span>
        {/each}
      {/if}
      <span class="breadcrumb__sep">/</span>
      <span class="breadcrumb__current">edycja</span>
    </nav>

    <div class="form">
      <input
        class="form__title"
        bind:value={title}
        placeholder="Tytuł notatki"
      />
      <textarea
        class="form__content"
        bind:value={content}
        placeholder="Treść w Markdown..."
        rows={20}
      ></textarea>

      {#if saveError}
        <p class="form__error">{saveError}</p>
      {/if}

      <div class="form__actions">
        <button class="btn btn--primary" onclick={handleSave} disabled={saving}>
          {saving ? 'Zapisywanie…' : 'Zapisz'}
        </button>
        <button class="btn btn--secondary" onclick={handleCancel}>Anuluj</button>
        <button class="btn btn--danger" onclick={handleDelete}>Usuń</button>
      </div>
    </div>
  </main>

  <style lang="scss">
    @use '$lib/styles/variables' as v;

    .page {
      max-width: 800px;
      margin: 0 auto;
      padding: v.$space-2xl v.$space-lg;
    }

    .breadcrumb {
      display: flex;
      align-items: center;
      gap: v.$space-xs;
      margin-bottom: v.$space-xl;
      font-size: 0.75rem;
      font-family: v.$font-mono;

      &__link {
        color: v.$accent-dark;
        text-decoration: none;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        transition: color 0.15s;

        &:hover { color: v.$accent; }
      }

      &__sep { color: v.$text-muted; }

      &__folder {
        color: v.$text-muted;
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }

      &__current {
        color: v.$text-muted;
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }
    }

    .form {
      display: flex;
      flex-direction: column;
      gap: v.$space-md;

      &__title {
        font-family: v.$font-mono;
        font-size: 1.4rem;
        color: v.$text-primary;
        background: transparent;
        border: none;
        border-bottom: 1px solid v.$border;
        padding: v.$space-xs 0;
        outline: none;
        width: 100%;

        &:focus { border-bottom-color: v.$accent-dark; }
      }

      &__content {
        font-family: v.$font-mono;
        font-size: 0.9rem;
        color: v.$text-primary;
        background: v.$bg-raised;
        border: 1px solid v.$border;
        border-radius: v.$radius-md;
        padding: v.$space-md;
        outline: none;
        resize: vertical;
        min-height: 300px;
        width: 100%;
        box-sizing: border-box;
        line-height: 1.6;

        &:focus { border-color: v.$accent-dark; }
      }

      &__error {
        font-family: v.$font-mono;
        font-size: 0.8rem;
        color: #c0392b;
        margin: 0;
      }

      &__actions {
        display: flex;
        gap: v.$space-sm;
        align-items: center;
      }
    }

    .btn {
      font-family: v.$font-mono;
      font-size: 0.8rem;
      padding: v.$space-sm v.$space-lg;
      border-radius: v.$radius-sm;
      border: 1px solid v.$border;
      cursor: pointer;
      transition: background 0.15s, color 0.15s;
      letter-spacing: 0.04em;
      text-transform: uppercase;

      &--primary {
        background: v.$accent-dark;
        color: v.$bg-deep;
        border-color: v.$accent-dark;

        &:hover:not(:disabled) { background: v.$accent; border-color: v.$accent; }
        &:disabled { opacity: 0.5; cursor: not-allowed; }
      }

      &--secondary {
        background: none;
        color: v.$text-secondary;

        &:hover { color: v.$text-primary; background: rgba(255,255,255,0.04); }
      }

      &--danger {
        background: none;
        color: #c0392b;
        border-color: transparent;
        margin-left: auto;

        &:hover { background: rgba(192, 57, 43, 0.1); }
      }
    }
  </style>
  ```

- [ ] **Step 3: Start the dev server and test the edit page**

  ```bash
  cd frontend && bun run dev
  ```

  1. Create a note via the inline input (Task 6 feature) — should land on the edit page.
  2. Type content, click Zapisz — should navigate to the note view with updated content.
  3. Click Anuluj — should navigate to note view without saving.
  4. Click Usuń, confirm — should navigate to the notes list.
  5. Click Usuń, cancel — should stay on the edit page.

- [ ] **Step 4: Commit**

  ```bash
  git add "frontend/src/routes/(protected)/workspace/[slug]/note/[id]/edit/"
  git commit -m "feat: add note edit page at /note/[id]/edit"
  ```

---

## Task 8: Add "Edytuj" button to note view page

**Files:**
- Modify: `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/+page.svelte`

- [ ] **Step 1: Add the Edytuj link to the note header**

  In `+page.svelte`, find the date/history line:

  ```svelte
  <p class="note-header__date">
    Zaktualizowano: {formatDate(note.updated_at)} ·
    <a href="/workspace/{slug}/note/{note.note_id}/history" class="note-header__history-link">Historia</a>
  </p>
  ```

  Replace with:

  ```svelte
  <p class="note-header__date">
    Zaktualizowano: {formatDate(note.updated_at)} ·
    <a href="/workspace/{slug}/note/{note.note_id}/history" class="note-header__history-link">Historia</a>
    ·
    <a href="/workspace/{slug}/note/{note.note_id}/edit" class="note-header__history-link">Edytuj</a>
  </p>
  ```

- [ ] **Step 2: Verify in the browser**

  Open any note. The "Edytuj" link should appear next to "Historia". Clicking it should load the edit page with the note's content pre-filled.

- [ ] **Step 3: Commit**

  ```bash
  git add "frontend/src/routes/(protected)/workspace/[slug]/note/[id]/+page.svelte"
  git commit -m "feat: add Edytuj link to note view page"
  ```

---

## Done

All tests pass (`uv run pytest -v`), the app compiles (`cd frontend && bun run check`), and the three user flows work:

1. Click `+` in NotesList → type title → Enter → lands on edit page
2. Edit content/title → Zapisz → back to note view with changes
3. Click Usuń on edit page → confirm → back to notes list, note gone
