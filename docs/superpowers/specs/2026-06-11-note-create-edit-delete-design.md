# Note Create / Edit / Delete — Design Spec

**Date:** 2026-06-11  
**Status:** approved

---

## Summary

Add the ability to create, edit, and delete notes directly from the frontend UI and via REST API. The backend service layer (`NoteService.save`, `update`, `delete`) and MCP tools already exist — this feature exposes them over HTTP and adds a frontend editor.

---

## Architecture

### REST API — three new endpoints

All endpoints live in `src/kajet_turbo/api/workspaces.py` and follow the existing auth + access-check pattern (session user, `has_access`).

| Method   | Path                                        | Purpose          |
|----------|---------------------------------------------|------------------|
| `POST`   | `/api/workspaces/{name}/notes`              | Create note      |
| `PATCH`  | `/api/workspaces/{name}/notes/{note_id}`    | Update note      |
| `DELETE` | `/api/workspaces/{name}/notes/{note_id}`    | Delete note      |

**POST body:** `{ title: str, content?: str = "", folder?: str = "", tags?: list[str] = [] }`  
**POST response (201):** `{ note_id: str }`  
Delegates to `NoteService.save()`. Returns 409 on duplicate title in folder.

**PATCH body:** `{ title?: str, content?: str, tags?: list[str], folder?: str }` — all fields optional  
**PATCH response (200):** `{ note_id: str }`  
Delegates to `NoteService.update()`. Returns 404 if note not found.

**DELETE response (200):** `{ ok: true }`  
Delegates to `NoteService.delete()`. Returns 404 if note not found.

**New schemas in `schemas.py`:**
- `CreateNoteRequest` / `CreateNoteResponse`
- `UpdateNoteRequest` / `UpdateNoteResponse`
- `DeleteNoteResponse`

After adding endpoints, regenerate Orval types (`uv run python scripts/export_openapi.py`).

---

## Frontend

### 1. Create note — inline in NotesList

Pattern mirrors folder creation in `FolderTree.svelte`.

- Header of `NotesList.svelte` gets a `+` button next to the note count.
- Click → shows an inline input row at the top of the list (placeholder: `tytuł-notatki`).
- Enter confirms, Escape cancels.
- On confirm: `POST /api/workspaces/{slug}/notes` with `{ title, folder: folderPath, content: "" }`.
- On success: `invalidate('app:ls')` + `goto(/workspace/{slug}/note/{note_id}/edit)`.
- On error (e.g. 409 duplicate): error string shown inline below the input.

`NotesList` receives an `onCreateNote(title: string): Promise<void>` callback prop (same pattern as `onCreateFolder` in `FolderTree`). The actual fetch lives in `+page.svelte`.

### 2. Edit note — `/workspace/[slug]/note/[id]/edit`

**New files:**
- `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/edit/+page.ts`
- `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/edit/+page.svelte`

**Data loading (`+page.ts`):**  
Fetch `/api/workspaces/{slug}/notes/{note_id}/markdown` (already exists). Returns `{ note_id, title, content, folder, tags, ... }`.

**Page layout (mirrors `/note/[id]` styling):**
- Breadcrumb (workspace / folder / title)
- `<input>` for title (pre-filled)
- `<textarea>` for content (pre-filled, responsive height)
- Button row: **Zapisz** | **Anuluj** | **Usuń**

**Zapisz:**  
`PATCH /api/workspaces/{slug}/notes/{id}` with `{ title, content }` → on success `goto(/workspace/{slug}/note/{id})`.

**Anuluj:**  
`goto(/workspace/{slug}/note/{id})` — no save.

**Usuń:**  
`window.confirm(...)` → on confirm `DELETE /api/workspaces/{slug}/notes/{id}` → on success `goto(/workspace/{slug}/notes/${folderPath})`.

### 3. Edit button on note view

`/workspace/[slug]/note/[id]/+page.svelte` gets an **Edytuj** link/button next to the history link, pointing to `/workspace/{slug}/note/{note_id}/edit`.

---

## Error Handling

| Status | Meaning          | UX                                  |
|--------|------------------|-------------------------------------|
| 401    | Not logged in    | Redirect to `/login`                |
| 403    | No access        | Inline error "Brak dostępu"         |
| 404    | Note not found   | Inline error                        |
| 409    | Duplicate title  | Inline error below input            |
| 5xx    | Server error     | Inline error from `body.error`      |

---

## Invalidation

| Action  | Invalidates        | Navigation                                 |
|---------|--------------------|--------------------------------------------|
| Create  | `app:ls`           | → `/workspace/{slug}/note/{id}/edit`       |
| Save    | `app:ls`           | → `/workspace/{slug}/note/{id}`            |
| Delete  | `app:ls`           | → `/workspace/{slug}/notes/{folderPath}`   |

`app:ls` is the existing depends key used by the explorer's `ls` fetch — invalidating it refreshes `NotesList` and `FolderTree`.

---

## Out of scope

- Tag editing (skipped for now)
- Auto-save / draft state
- Rename/move note from explorer (handled via edit page `folder` field in future)
