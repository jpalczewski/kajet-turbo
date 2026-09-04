# Notes Service Layer

This package (`NoteService` plus its collaborators `NoteTagService`, `NoteFolderService`,
`NoteLinkService`) is the synchronous, request-facing CRUD layer for notes — called directly
from API routes and MCP tools, not dispatched by job kind. That is the boundary between this
package and the flat `services/` directory: background job handlers (`embed_handler.py`,
`push_handler.py`, `reconcile_links_handler.py`, `reindex_handler.py`) live there regardless of
which domain they touch, registered once in `register_job_handlers()` (`server.py:48`). A new
background handler does not belong in this package even if it operates on notes.

## Note-body writes go through `staged_workspace_change`

`staged_change.py`'s `StagedChange`/`staged_workspace_change` is the write-commit-rollback
pipeline for touching a note's file on disk: snapshot every `add`/`remove` path's bytes,
apply every item, commit once, and restore every snapshot if a later item in the batch fails.
Restore is derived from the snapshot, not supplied by the caller — a rollback is byte-exact by
construction, unlike a hand-rebuilt frontmatter object. `add`+`remove` on one item also makes a
rename one commit instead of two (see `update()`'s rename leg). Every path that writes note
content already goes through it — either directly, or via `commit_rows_then_tree`/
`commit_rows_then` below (single save, batch save, rename/update, `edit_many`,
`apply_temporal_backfill`, single-note tagging, `rename_tag`, `_rewrite_backlinks`, `move`,
`move_folder`), or directly only for `reconcile_paths`'s adoption path. Reuse it for any new
note-body write path — do not hand-roll the stage/write/commit/rollback sequence again.

Pure identity changes that never touch note content — `move()`, `move_folder()` — use the same
`StagedChange`/`staged_workspace_change` primitive (`move()`) or `GitRepository.commit_changes`
directly (`move_folder()`, whose temp-dir choreography already has its own correct rollback for
the filesystem-move phase), not because they write a note body but because the primitive is the
one place that knows how to commit `add`/`remove` pairs atomically. Both are now wrapped by
`commit_rows_then_tree`/`commit_rows_then` respectively, so the DB folder-column update commits
in the same transaction as every other write path here, last — see #155 below for what that
still doesn't cover for `move_folder`.

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

Every note-row write path in this package now goes through `commit_rows_then_tree` or its
lower-level sibling `commit_rows_then`: `_apply_tag_change`, `rename_tag`, `_rewrite_backlinks`,
and `move` call `commit_rows_then_tree`. `_rewrite_backlinks`'s row update carries forward only
`updated_at` (an unchanged passthrough) and bumps `index_generation`; `occurred_at`/`period` are
deliberately never resynced from the file there (#125) — the method's own docstring claim that
it never touches dates is now enforced by the code, not just asserted by it. `move_folder`'s
tree mutation — a temp-dir rename choreography that physically moves every file before git is
involved at all — isn't expressible as `StagedChange` items, so it calls `commit_rows_then`
directly with its own `commit_tree` closure wrapping `GitRepository.commit_changes`. That
choreography's own rollback only covers a failure *during* the move itself: once it completes,
a `write_rows`/`commit_tree` failure inside `commit_rows_then` rolls the DB rows back together,
but the files are already at their new location on disk with no git commit recording it.

This is a real behavior change, not just a narrower version of the old gap: before this fix,
`move_folder` ran its git commit *first*, unconditionally, so a plain DB-write failure always
left git and disk agreeing with each other and only the rows lagging — exactly the direction
`reconcile_paths` heals (it re-derives a row's `folder` from wherever its file is actually
found on disk). Now a DB-write failure alone can leave git stale relative to disk too, and
`reconcile_paths` never touches git — it only rewrites rows. Once it heals the row to match
the file's new location, git history for that note's *new* path is empty (nothing was ever
committed there), which is what `get_note_history`/`get_version`/`restore_note_version` query
by path. A `git push` of HEAD at that point still contains the old path's blob and lacks the
new one, so the remote diverges from local disk for that note until something else touches it.
`move_folder`'s conversion to `commit_rows_then` traded "DB can lag, self-healing" for "git can
lag, no repair path" on this one failure mode — a real, not just narrower, gap.

Separately: `rename_tag` and `_rewrite_backlinks` can now touch every note in a workspace (a
tag applied everywhere, a heavily-linked hub note being renamed) in one `commit_rows_then_tree`
call, same as `move_folder` can touch every note under a large folder. All three now hold the
single shared SQLite write lock for the DB rows *and* the multi-file git commit that follows,
in one transaction — a cost the five original `commit_rows_then_tree` callers (`save`,
`save_many`, `update`, `edit_many`, `apply_temporal_backfill`) already accepted, but only ever
at a caller-bounded batch size. These three have no such bound.

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

`NoteLinkService._rewrite_backlinks` (`links.py:356-460`) rewrites wikilink text in every note
that links to something just moved/renamed, then writes the DB row and commits directly (rows
first, one transaction, same #155 ordering as everything else here) — bypassing `update()`'s
pipeline for three of its four post-write steps, addressing the fourth (search reindexing) with
a `reindex_note` job enqueued in the same transaction instead. The docstring at that call site
enumerates and justifies each. It is not accidental duplication, it is a deliberate divergence
for batch-commit atomicity. The one thing to keep in sync from here: if `update()`'s pipeline
gains or reorders steps, that enumeration is the one place that has to be revisited by hand —
nothing enforces it automatically.

## Service boundaries

`NoteService` owns the indexer, `_locate_batch`/`_LocatedNote`, and the write pipeline.
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
