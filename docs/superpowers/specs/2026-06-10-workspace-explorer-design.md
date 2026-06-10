# Workspace Explorer Design

**Date:** 2026-06-10
**Status:** Approved

## Overview

Replace the flat notes list (`/workspace/[slug]/notes`) with a 3-panel file explorer. Folder tree on the left, notes list in the middle, note preview on the right. URL encodes full navigation state via a catch-all path parameter.

---

## URL Scheme

Route: `/workspace/[slug]/notes/[[...path]]` (SvelteKit optional catch-all)

```
/workspace/slug/notes/                        → root folder, no note open
/workspace/slug/notes/docs/                   → folder docs/, no note open
/workspace/slug/notes/docs/guide/             → folder docs/guide/, no note open
/workspace/slug/notes/docs/guide/abc1234      → folder docs/guide/, note abc1234 open
```

**Parsing logic in `+page.ts`:**

1. Try `GET /api/workspaces/{name}/ls?path={fullPath}` — if 200, it's a folder view.
2. If 404, treat last segment as `note_id`, remaining segments as folder path.

Note IDs are 7-character nanoids (e.g. `abc1234`). No regex needed — disambiguation is done via the `/ls` endpoint response.

The existing `/workspace/[slug]/note/[id]` route (singular) stays untouched for backwards compatibility.

---

## Layout

Three-panel explorer, inspired by file managers and VS Code:

```
┌─────────────────┬──────────────────────┬─────────────────────┐
│  SIDEBAR        │  NOTES LIST          │  NOTE PREVIEW       │
│  (folder tree)  │  (current folder)    │  (selected note)    │
│                 │                      │                      │
│  ▼ docs/        │  intro.md   2.1 KB   │  # Intro            │
│    ▶ api/       │  setup.md   1.8 KB   │                      │
│    ▼ guide/ ●   │                      │  Lorem ipsum...      │
│  ▶ notes/       │                      │                      │
│  README.md      │                      │                      │
└─────────────────┴──────────────────────┴─────────────────────┘
```

- **Sidebar**: folder tree, loaded once via `/ls?recursive=true`, collapsible nodes.
- **Middle panel**: notes in selected folder via `/notes?folder=X`, shows `title` + `size_bytes` + `updated_at`.
- **Right panel**: note HTML content via `/notes/{id}/html`, shown when a note is selected.
- **Initial state**: root folder selected, root-level notes shown in middle panel, right panel empty.

---

## Backend Changes

### 1. Fix async anti-pattern

GET endpoints in `workspaces.py` changed from `async def` to `def`. FastAPI runs sync handlers in a thread pool automatically. POST endpoints that use `await request.json()` stay as `async def`.

### 2. New endpoint: `GET /api/workspaces/{name}/ls`

**Query params:**
- `path: str = ""` — folder path relative to workspace root
- `recursive: bool = False` — if true, returns full folder tree (no entries)

**Response schema `LsResponse`:**
```python
class LsEntry(BaseModel):
    note_id: str
    title: str
    size_bytes: int
    updated_at: str

class LsResponse(BaseModel):
    folders: list[str]   # direct subfolders (non-recursive) or all folders (recursive)
    entries: list[LsEntry]  # notes at this path; empty when recursive=True
```

**Non-recursive behavior** (`recursive=False`):
- `folders`: direct child folder names at `path` (not full paths, just the last segment)
- `entries`: notes where `note.folder == path`, with `size_bytes` from `Path.stat().st_size`

**Recursive behavior** (`recursive=True`):
- `folders`: all folder paths in workspace, fully expanded including intermediate segments
  - e.g. notes with `folder="docs/guide"` → produces `["docs", "docs/guide"]`
- `entries`: empty (sidebar only needs structure, not individual notes)

### 3. New `NoteRepository` method

```python
def list_folders(self, workspace: str, owner_id: str) -> list[str]:
    """Returns all distinct non-empty folder values for this workspace."""
```

Used by `/ls?recursive=true` to build the full folder tree from DB without filesystem walk.

### 4. `NoteItem` schema gets `size_bytes`

```python
class NoteItem(BaseModel):
    ...
    size_bytes: int
```

Computed from `Path(note_filepath(ws_path, note.folder, note.title)).stat().st_size` in the list handler. Existing `/notes` endpoint updated to include this field.

---

## Frontend Changes

### Route restructure

| Before | After |
|--------|-------|
| `/workspace/[slug]/notes/+page.svelte` | `/workspace/[slug]/notes/[[...path]]/+page.svelte` |
| `/workspace/[slug]/notes/+page.ts` | `/workspace/[slug]/notes/[[...path]]/+page.ts` |

### `+page.ts` data loading

```typescript
// Parse path
const segments = params.path?.split('/').filter(Boolean) ?? []

// Disambiguate: try full path as folder
const lsResult = await fetch(`/api/workspaces/${slug}/ls?path=${segments.join('/')}`)
const isFolder = lsResult.ok

const folderPath = isFolder ? segments.join('/') : segments.slice(0, -1).join('/')
const noteId = isFolder ? null : segments.at(-1) ?? null

// Parallel loads
const [tree, notes, note] = await Promise.all([
  fetch(`/api/workspaces/${slug}/ls?recursive=true`),
  fetch(`/api/workspaces/${slug}/notes?folder=${folderPath}`),
  noteId ? fetch(`/api/workspaces/${slug}/notes/${noteId}/html`) : null,
])
```

### New Svelte components

- **`FolderTree.svelte`** — sidebar component; receives flat folder list, builds tree, renders collapsible nodes; highlights active folder; emits navigation via SvelteKit `goto()`.
- **`NotesList.svelte`** — middle panel; receives `NoteItem[]`; renders title + size + date; clicking a note appends note_id to current path via `goto()`.
- **`NotePreview.svelte`** — right panel; receives note HTML; renders sanitized HTML (reuses existing note detail rendering); empty state when no note selected.

---

## Data Flow Summary

```
URL change
  └─ +page.ts runs
       ├─ /ls?recursive=true       → FolderTree (sidebar)
       ├─ /notes?folder=X          → NotesList (middle)
       └─ /notes/{id}/html         → NotePreview (right, optional)
```

All three requests fire in parallel via `Promise.all`. SvelteKit's `invalidate()` handles re-fetching on navigation.

---

## Out of Scope

- Creating/editing notes from the explorer (still done via MCP or dedicated routes)
- Mobile layout (sidebar collapses to full-width on narrow screens — deferred)
- Drag-and-drop file moving
