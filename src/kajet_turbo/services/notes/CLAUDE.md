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
one place that knows how to commit `add`/`remove` pairs atomically. `move()` is wrapped by
`commit_rows_then_tree`, so its DB row update commits in the same transaction as the git commit,
last, like every other write path here. `move_folder()` deliberately is NOT — see #155 below.

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

Every note-row write path in this package but one goes through `commit_rows_then_tree` or its
lower-level sibling `commit_rows_then`: `_apply_tag_change`, `rename_tag`, `_rewrite_backlinks`,
and `move` call `commit_rows_then_tree`. `_rewrite_backlinks`'s row update carries forward only
`updated_at` (an unchanged passthrough) and bumps `index_generation`; `occurred_at`/`period` are
deliberately never resynced from the file there (#125) — the method's own docstring claim that
it never touches dates is now enforced by the code, not just asserted by it.

`move_folder`'s tree mutation — a temp-dir rename choreography that physically moves every file
before git is involved at all — isn't expressible as `StagedChange` items and, unlike every
other write path here, deliberately does not go through `commit_rows_then`/
`commit_rows_then_tree` at all (#170). It commits the git tree unconditionally *first*
(`GitRepository.commit_changes`, matching this call site's pre-#155 behavior), then writes every
folder-column update in one DB transaction of its own, bounded above by `_MOVE_FOLDER_MAX_NOTES`
(`folders.py`) rather than chunked — a plain `crud_repo.operation()` block of at most a few
thousand row updates, no git commit inside it, is not worth trading atomicity for. This is a
deliberate carve-out, not an oversight: the row-then-tree invariant's rationale ("rows first
because SQL is cheap to fail before anything has touched disk") never applied here anyway, since
the temp-dir choreography has already mutated disk unconditionally before either commit path
runs — routing `move_folder` through `commit_rows_then` bought none of that guarantee while
adding a new failure mode (a DB-write failure could roll back rows *and* skip the git commit,
leaving git permanently stale relative to disk with no repair path, since `reconcile_paths` only
heals rows, never touches git).

Git-first ordering restores the pre-#155 property instead: a DB-write failure can only ever
leave rows *behind* an already-committed tree — the direction `reconcile_paths` heals — never
the reverse. `move_folder` also has its own `crud_repo.operation(...)` handle directly (it isn't
behind a `write_rows` closure passed into a shared helper), so a folder move touching zero notes
(only aux files) opens zero `repository_operation` calls instead of logging a spurious
`count=0` line (#172).

Separately: `rename_tag` and `_rewrite_backlinks` can still touch every note in a workspace (a
tag applied everywhere, a heavily-linked hub note being renamed) — unlike the five original
`commit_rows_then_tree` callers (`save`, `save_many`, `update`, `edit_many`,
`apply_temporal_backfill`), whose batch size is always caller-bounded. Both now chunk their
writes to `MAX_BATCH_COMMIT_SIZE` (500) items per `commit_rows_then_tree` call/transaction/git
commit (`staged_change.py`) instead of one unbounded call for the whole batch (#171), trading
single-commit atomicity for bounded *SQLite* write-lock hold time (each chunk's transaction
starts and commits independently) and bounded `repository_operation` log-line size (a
`note_ids` field per chunk restores the per-note traceability #173 lost). This does NOT bound
the coarser `@workspace_write_transaction` lock both methods run under end to end — that lock
(an in-process RLock plus a cross-process flock, `git.py`) is already held for the whole call
regardless of chunking, same as before this fix; chunking only shrinks how long any single
chunk holds SQLite's own write lock, which is what #171 was about (a global lock shared by
every workspace, unlike the per-workspace `@workspace_write_transaction` lock).

`rename_tag` gets resumability from this too — a re-run after a mid-batch failure picks up
exactly the unprocessed remainder, since `note_ids_for_tags` reads live join-table state, and
the join-table sync for a chunk runs inside that chunk's own transaction (not as a separate
call after it) so a chunk's rows, tags, and tree commit succeed or fail together. The
already-renamed notes carry the target tag, so the retry needs `merge=True`, same as renaming
onto any other pre-existing tag. The per-workspace orphan-tag sweep and the search-reindex
dispatch both run once after the whole chunk loop, not once per chunk — each would otherwise
redo a full-workspace scan (sweep) or fire a redundant deferred callback (reindex) per chunk
for no correctness benefit.

`move_folder`'s own DB write, by contrast, stays a single unchunked transaction (see above) —
it doesn't need this trade-off since no git commit runs inside it — and rejects outright above
`_MOVE_FOLDER_MAX_NOTES` (5000) before its disk move begins: unlike a tag rename or backlink
rewrite, an oversized folder move is expensive and irreversible before either commit path runs,
and the tool exposes a real workaround (move a subfolder at a time). Renaming a popular *flat*
tag has no equivalent workaround; renaming a tag that already has a narrower subtree
(`note_ids_for_tags` matches by prefix) can be split into per-subtree renames instead.

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

`NoteService` owns the indexer and the write pipeline. Shared batch reads use the neutral
`locate_many` helper in `locator.py`, which returns `workspace.LocatedNote` values and leaves
validation policy with its callers.
`NoteTagService`, `NoteFolderService`, and `NoteLinkService` are collaborators that, by default,
operate on metadata only — `NoteFolderService.move_folder` needs no indexer because a folder
move never touches note bodies. A method on one of these collaborators that starts writing note
*bodies* (not just frontmatter/DB rows) is a signal that its placement needs a deliberate call,
not a default. #57 (`rename_tag` moving from `NoteTagService` to `NoteService`) is the worked
example of how that call gets made and what tips it one way or the other.

## Errors

A `ValueError` raised anywhere in this package reaches the calling LLM verbatim —
`logged_tool` maps it straight to `ToolError` (see root `CLAUDE.md`). Write the
message in English and name the parameter at fault.
