# Delete note from NoteActions + generic ConfirmDialog

**Date:** 2026-06-27

## Goal

Add note deletion accessible from `NoteActions` (the action bar visible when a note is open in both `preview` and `full` variants). Simultaneously refactor the existing `window.confirm` in the edit page into a proper modal, using a new generic `ConfirmDialog` component.

## Scope

- New component: `frontend/src/lib/components/ui/ConfirmDialog.svelte`
- Modified: `frontend/src/lib/components/note/NoteActions.svelte`
- Modified: `frontend/src/routes/(protected)/workspace/[slug]/note/[id]/edit/+page.svelte`
- No backend changes — `DELETE /api/workspaces/{name}/notes/{note_id}` already exists and is covered by the generated API client (`apiDeleteNoteApiWorkspacesNameNotesNoteIdDelete`).

## Component: `ConfirmDialog.svelte`

Generic confirm modal. Does not render its own trigger — callers supply a trigger via a Svelte 5 snippet prop, which receives an `open` function.

**Props:**

| Prop | Type | Description |
|------|------|-------------|
| `title` | `string` | Modal heading |
| `message` | `string` | Body text shown to the user |
| `confirmLabel` | `string` | Label for the confirm button |
| `confirmVariant` | `'primary' \| 'danger'` | Style variant for the confirm button |
| `onconfirm` | `() => Promise<void>` | Async callback executed on confirm |
| `trigger` | `Snippet<[{ open: () => void }]>` | Snippet that renders the trigger element |

**Internal state:** `isOpen: boolean`, `loading: boolean`, `error: string`.

**Behavior:**
- `{@render trigger({ open: () => isOpen = true })}` renders the caller's trigger
- On confirm: sets `loading = true`, calls `onconfirm()`, closes on success, shows `error` on failure
- Cancel button and clicking outside the modal close it without action

**Wraps** the existing `Modal` component (`$lib/components/ui/Modal.svelte`).

## `NoteActions.svelte` changes

**New props:**

| Prop | Type | Description |
|------|------|-------------|
| `noteTitle` | `string` | Displayed in the confirm message |
| `ondeleted` | `() => void \| Promise<void>` | Called after successful deletion; caller handles navigation |

**New action:** `ConfirmDialog` with a `<button class="actions__link">Usuń</button>` trigger, placed after `MoveNoteDialog`. The `onconfirm` callback:
1. Calls `apiDeleteNoteApiWorkspacesNameNotesNoteIdDelete(slug, noteId)`
2. Calls `invalidate('app:workspace-tree')`
3. Calls `ondeleted()`

Both `preview` and `full` variants receive delete — no reason to differentiate.

**Callers of `NoteActions`** must supply `noteTitle` and `ondeleted`. The `ondeleted` handler navigates to `notesPath(slug, folder)`.

## Edit page (`/note/[id]/edit`) changes

Removes `window.confirm`. Replaces the existing `<button class="btn btn--danger" onclick={handleDelete}>Usuń</button>` with a `ConfirmDialog` whose trigger snippet renders the same button. The `onconfirm` callback contains the existing delete logic (API call → `invalidate` → `goto(notesPath(...))`).

## Navigation after deletion

Both paths (NoteActions and edit page) navigate to `notesPath(slug, note.folder ?? '')` — the folder the note belonged to.

## Out of scope

- Deletion from `NotesList` (middle column) — not requested
- Batch deletion
- Soft delete / trash
