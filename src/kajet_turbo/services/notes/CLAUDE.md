# Notes Service Layer

This package (`NoteService` plus its collaborators `NoteTagService`, `NoteFolderService`,
`NoteLinkService`) is the synchronous, request-facing CRUD layer for notes — called directly
from API routes and MCP tools, not dispatched by job kind. That is the boundary between this
package and the flat `services/` directory: background job handlers (`embed_handler.py`,
`push_handler.py`, `reconcile_links_handler.py`) live there regardless of which domain they
touch, registered once in `register_job_handlers()` (`server.py:48`). A new background handler
does not belong in this package even if it operates on notes.

## Note-body writes go through `staged_workspace_change`

`staged_change.py`'s `StagedChange`/`staged_workspace_change` is the write-commit-rollback
pipeline for touching a note's file on disk: snapshot every `add`/`remove` path's bytes,
apply every item, commit once, and restore every snapshot if a later item in the batch fails.
Restore is derived from the snapshot, not supplied by the caller — a rollback is byte-exact by
construction, unlike a hand-rebuilt frontmatter object. `add`+`remove` on one item also makes a
rename one commit instead of two (see `update()`'s rename leg). Every path that writes note
content already goes through it — either directly, or via `commit_rows_then_tree` below (single
save, batch save, rename/update, `edit_many`, `apply_temporal_backfill`), or directly for
`reconcile_paths`'s adoption path, single-note tagging, `rename_tag`, and `_rewrite_backlinks`.
Reuse it for any new note-body write path — do not hand-roll the stage/write/commit/rollback
sequence again.

Pure identity changes that never touch note content — `move()`, `move_folder()` — use the same
`StagedChange`/`staged_workspace_change` primitive (`move()`) or `GitRepository.commit_changes`
directly (`move_folder()`, whose temp-dir choreography already has its own correct rollback for
the filesystem-move phase — a `commit_changes` failure *after* that phase is a separate,
pre-existing gap tracked by #155, not something this pairing claims to cover), not because they
write a note body but because the primitive is the one place that knows how to commit
`add`/`remove` pairs atomically.

## The row transaction wraps the git commit, and commits last (#155)

The file tree is the source of truth; `notes` rows are a derived index. On every write, the
**note-row transaction wraps the git commit and commits last**: a `GitError`/`OSError` rolls
the rows back and `staged_workspace_change` restores the tree, so nothing moved. The only
residual window is "git committed, SQLite COMMIT failed", which leaves the tree *ahead of* the
index — the direction `reconcile_paths` heals. A write path must never be able to leave the
index ahead of the tree.

`staged_change.py`'s `commit_rows_then_tree` is this shape as one helper: it opens a
`crud_repo.operation(...)` transaction, runs the caller's `write_rows(session)` (a call into
`insert_in_session`/`update_in_session` — SQL is cheap to fail before anything touches disk),
`flush()`s so a constraint violation surfaces before the tree write rather than at COMMIT, then
commits the git tree via `staged_workspace_change` inside the same transaction. `save`,
`save_many`, `update`, `edit_many`, and `apply_temporal_backfill` all call it — this is the
shared batch skeleton #144 asked for; `edit_many` and `apply_temporal_backfill` are two of its
callers, not two parallel implementations. `delete`/`delete_many` predate this helper and use
`GitRepository.delete_file(s)` directly instead of `staged_workspace_change`, but follow the
same rows-first-commit-last ordering.

`rename_tag`, `_apply_tag_change`, `_rewrite_backlinks`, and `move`/`move_folder` do **not**
go through `commit_rows_then_tree` yet — their DB write still runs after the git commit. That
gap is tracked, not accidental; do not assume every write path here already has the #155
invariant just because most of them now do.

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

`NoteLinkService._rewrite_backlinks` (`links.py:309-416`) rewrites wikilink text in every note
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
