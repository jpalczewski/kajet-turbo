# Repository Layer

Every class here owns one table (or one virtual-table family) and nothing above it: no git,
no cache, no service policy. `repository_name` is the *table* name, not the module path —
`notes/crud.py` is `"notes"`, `notes/links.py` is `"note_links"` (`crud.py:27`,
`links.py:10`) — because it is what lands in the `repository` field and prefixes
`operation` in every `repository_operation` line, and log queries are written against
tables, not modules.

`git.py`'s `GitRepository` is the one file here that is not a `DbRepository`: a
Dulwich/filesystem repository that shares the layer's name and shape only. Root
`CLAUDE.md`'s `DbRepository` rules do not apply to it; its locking rules do.

## `log_operation()` is the third route, and it does not survive interleaving

Root `CLAUDE.md` names two: `timed_session()` for quiet reads, `operation()` for mutations.
The third is `timed_session()` followed by `log_operation()`, for a line whose fields are
only known once the session has closed — a `rowcount` off a `CursorResult`
(`folder_meta.py:127-132`), a `job_id` returned by a nested enqueue
(`link_reconcile.py:52-59`), a match count computed in Python after the query
(`crud.py:230-237`).

It works off a `ContextVar` written when a `timed_session()` **exits**, keyed by `id(self)`
(`__init__.py:70-90`). That is the sharp edge: `log_operation()` reports the last
`timed_session` *this* repository closed, and raises `RuntimeError` if the last one to close
belonged to another repository (`__init__.py:87-89`). So nothing between the block's exit
and the `log_operation()` call may open and close a session on a different repository. Keep
the two adjacent, or use `operation()` — which needs no late field and cannot be interleaved
out from under itself.

Separately: when a repository composes another one inside its own session, it calls the
`_in_session` variant. `LinkReconcileRepository.mark_and_enqueue` uses
`JobRepository.enqueue_in_session` (`link_reconcile.py:45`) so the dirty markers and the
job row commit together, and so no second write session opens against SQLite while the
outer one already holds the write lock. Calling `enqueue()` there would cost both.

`#142` tracks replacing the `ContextVar` coupling above with an explicit return value —
the ordering hazard this section describes is a known, tracked wart, not an accepted
permanent shape.

## Index generation is a cross-process compare-and-swap

Nothing locks an editing request against the indexing worker — they are routinely in
different processes. `notes.index_generation` is what keeps them honest, and it takes three
cooperating pieces:

- The writer bumps it **iff the note's indexed text changed — body or title**.
  Chunks and the `notes_fts` rows are built from title + content (`chunks.py:184-196`), so
  a rename invalidates the index exactly as an edit does; that is why `NoteService.update()`'s
  rename/move leg passes `True`. `services/notes/tags.py:355` passes `item.body_changed`
  because tags are stripped before indexing; `apply_temporal_backfill`'s row write passes
  `False` because it rewrites frontmatter dates only.
- The indexer passes the generation it read as `expected_generation`, and `replace_chunks`
  makes its *first* statement a conditional `UPDATE ... WHERE index_generation = :expected
  RETURNING id` (`chunks.py:125-137`). That statement takes SQLite's write lock, so a
  superseded indexer rolls back with `outcome="superseded"` instead of deleting a newer
  edit's chunks.
- The deferred-embedding path has no generation to compare against, so `attach_vectors`
  substitutes set-equality on the stored chunk ids, checked inside the same transaction
  (`chunks.py:236`), and no-ops if they moved.

Bumping on a metadata-only edit is not a harmless conservative choice: it discards in-flight
indexing work and re-enqueues embedding for a note whose indexed text did not change. In
every branch the recovery story is the same — leave the note `stale` and let the edit's own
follow-up job repair it. Never patch around a lost CAS by writing anyway.

`LinkReconcileDirty.generation` is the same shape on a different table: the mark bumps it
(`link_reconcile.py:35-42`), the worker snapshots, and `acknowledge` deletes only markers
whose generation still matches (`link_reconcile.py:75-99`) — so a write that landed while
the worker ran is not silently acknowledged away.

## `session.execute()` with an inline `ty: ignore` is the house style

SQLModel's `session.exec()` cannot type a Core `delete()`, a `text()` statement, or a SQLite
`INSERT ... ON CONFLICT`, and ty flags the `execute()` fallback as deprecated. The project's
answer is `execute()` plus an inline ignore — there are ~110 of them in this package
(`oauth.py` alone has 42). This is convention, not debt; do the same in new code:

```python
session.execute(  # ty: ignore[deprecated] - raw SQL
    text("DELETE FROM sessions WHERE token = :token"), {"token": token}
)
```

Reading `.rowcount` off the result needs a second suppression — `# ty:
ignore[unresolved-attribute] - CursorResult at runtime` (`sessions.py:52`) — or an
`assert isinstance(result, CursorResult)`, which narrows the type properly and is the
better choice in new code (`folder_meta.py:127`, `events.py:102`). Both spellings are live.

Reuse one of the reason texts already in the package (`raw SQL`, `DELETE statement`,
`sqlite INSERT ON CONFLICT requires execute(), not exec()`) rather than inventing a new
phrasing. Normalize stragglers in code you are already changing; do not open a pass over
the rest.

`#141` tracks collapsing the ~96 occurrences of this shape into one `DbRepository` helper.
Once it lands, call that helper instead of repeating the pattern by hand.

## A returned row must be safe to read after its session closes

`commit()` expires every instance in the session, and leaving the `with` block detaches
them — so an ORM instance handed back by a committing method raises on the caller's first
attribute access. Three ways out, all in use, in order of preference:

- Build the return value before the commit, or from raw row data. `EventRepository.publish`
  holds `event_id` in a local precisely because reading `event.id` afterwards would refresh
  a detached object (`events.py:33-36`); `JobRepository.claim` returns `Job(**row._mapping)`,
  constructed outside the identity map (`jobs.py:167`).
- Return a narrow non-ORM type. `OutboxEvent` exists for this reason and documents it
  (`events.py:14-26`); the listing methods return `list[dict]`.
- If the ORM instance genuinely is the return value, `session.refresh()` it after the commit
  (`ssh_keys.py:66`, `embedding_profiles.py:62`, `workspace_remote.py:57`).

Read-only methods are exempt: no commit means no expiry, which is why `NoteRepository.get`
can hand back a live `Note` (`crud.py:79-84`).

## The signature is the transaction contract — the name is not always

Root `CLAUDE.md` states the contract for a caller-owned session (no commit, no timing, no
log). Most of the package marks it with an `_in_session` suffix — `links.py:82-101`,
`crud.py:63`, `tags.py:125`, all `@staticmethod`, session first, which makes "cannot open
its own session" structural rather than a promise. Prefer that shape, with that suffix, for
anything new.

`delete_for_workspace` is the tracked exception, not a second valid spelling:
`NoteChunkRepository` (`notes/chunks.py:495`) and `NoteRepository` (`notes/crud.py:453`)
take a caller-owned `session` and don't commit — same contract as the `_in_session` group —
but carry the bare name, same as five *other* repositories
(`dangling_links.py:144`, `folder_meta.py:119`, `active_workspace.py:57`, `jobs.py:310`,
`link_reconcile.py:102`) that own their session and commit themselves. `#139` tracks the
rename. Until it lands: read the signature before calling `delete_for_workspace` anywhere,
don't assume from the name.

One ordering constraint rides on the caller-owned pair regardless of naming:
`note_chunks.note_id` is an FK to `notes.id` with no cascade, so chunks must be deleted
before notes within the same session (`chunks.py:497-498`, `crud.py:455-456`).
