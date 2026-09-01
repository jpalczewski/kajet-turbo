# Notes Service Layer

This package (`NoteService` plus its collaborators `NoteTagService`, `NoteFolderService`,
`NoteLinkService`) is the synchronous, request-facing CRUD layer for notes — called directly
from API routes and MCP tools, not dispatched by job kind. That is the boundary between this
package and the flat `services/` directory: background job handlers (`embed_handler.py`,
`push_handler.py`, `reconcile_links_handler.py`) live there regardless of which domain they
touch, registered once in `register_job_handlers()` (`server.py:48`). A new background handler
does not belong in this package even if it operates on notes.

## Note-body writes go through `staged_note_write`

`staged_write.py`'s `StagedWrite`/`staged_note_write` is the write-commit-rollback pipeline for
touching a note's file on disk: stage every file, write them, commit once, and roll back
whichever ones were already written if a later one in the batch fails. Every path that writes
note content already goes through it — single save (`service.py:451`), batch save
(`service.py:603`), rename/move (`service.py:1023`), `edit_many` (`service.py:1222`),
`delete_many`/reconcile (`service.py:1501`, `1637`), single-note tagging (`tags.py:165`),
`rename_tag` (`tags.py:364`). Reuse it for any new note-body write path — do not hand-roll the
stage/write/commit/rollback sequence again.

## Link resolution has two consistency tiers

`note_links` is eager only for the note being saved: `NoteLinkService.persist`
(`links.py:145-148`) runs synchronously on every write path, so a note's own outgoing edges are
always correct by the time its save call returns. For *other* notes whose resolution can
change because of that write — a rename or move elsewhere changing what a bare `[[Title]]`
link resolves to — repair is lazy: `ReconcileLinksHandler` drains `LinkReconcileDirty` markers
in the background (`reconcile_links_handler.py`). Code reading `note_links` directly (a bulk
query, a graph view, anything outside the single-note `backlinks`/`outlinks` path) must account
for this: right after a rename elsewhere, a stale edge can briefly still be there.

## `_rewrite_backlinks` deliberately bypasses `NoteService.update()`

`NoteLinkService._rewrite_backlinks` (`links.py:294-402`) rewrites wikilink text in every note
that links to something just moved/renamed, then commits and updates the DB row directly —
skipping four steps `update()` normally runs. The docstring at that call site enumerates and
justifies each skipped step; it is not accidental duplication, it is a deliberate divergence
for batch-commit atomicity. The one thing to keep in sync from here: if `update()`'s pipeline
gains or reorders steps, that enumeration is the one place that has to be revisited by hand —
nothing enforces it automatically.

## Service boundaries

`NoteService` owns the indexer, cache, `_locate_batch`/`_LocatedNote`, and the write pipeline.
`NoteTagService`, `NoteFolderService`, and `NoteLinkService` are collaborators that, by default,
operate on metadata only — `NoteFolderService.move_folder` needs no indexer because a folder
move never touches note bodies. A method on one of these collaborators that starts writing note
*bodies* (not just frontmatter/DB rows) is a signal that its placement needs a deliberate call,
not a default. #57 (`rename_tag` moving from `NoteTagService` to `NoteService`) is the worked
example of how that call gets made and what tips it one way or the other.

## Errors

A `ValueError` raised anywhere in this package reaches the calling LLM verbatim —
`ServiceErrorMiddleware` maps it straight to `ToolError` (see root `CLAUDE.md`). Write the
message in English and name the parameter at fault.
