# Workspace specs

A living reference for how a workspace stores and resolves notes. Update this
alongside the code it describes — unlike `docs/superpowers/specs/`, this is not a
point-in-time design log.

## Workspace = a git repo of markdown notes

Each user workspace is a Dulwich git repo on disk (`GitRepository`,
`kajet_turbo/repositories/git.py`). Every note is one markdown file with YAML
frontmatter (`write_note_file`/`read_note_file`, `kajet_turbo/workspace.py`); the DB
(`Note` table) is a queryable index over the same data, not the source of truth —
the git history is.

## Folders and filenames

A note's `(folder, title)` maps to a file path via `note_filepath` /
`title_to_windows_filename` (`workspace.py:19-27,133-135`): forbidden Windows
characters (`\ / : * ? " < > |`, control chars) are stripped, trailing dots/spaces
are trimmed, and a Windows-reserved name (`CON`, `NUL`, `COM1`, …) gets a `_`
prefix. Letter case is preserved exactly.

**Known limitation — case-only collisions on case-insensitive filesystems.**
`check_unique` (`repositories/notes/crud.py:52`) is a case-sensitive SQL equality
check, so two notes differing only by title case (`Readme` / `readme`) in the same
folder both pass it and get written as two distinct files. That's correct on the
prod host (Linux, case-sensitive filesystem), but if that workspace's git repo is
ever checked out on a case-insensitive filesystem — the default on Windows (NTFS)
and macOS (APFS) — the two files collide into one, silently. `folders.py:144`
already routes around a related case-only-rename self-collision (moving through a
temp dir). This is a known, currently unaddressed gap — not solved by the wikilink
change below — tracked separately once an issue exists for it.

## Wikilinks — resolution semantics

Wikilink syntax and ranking rules for suffix/ambiguity matching live in
`markdown/link_index.py` (`LinkIndex`) — see that module's docstring for the full
suffix/proximity ranking contract. This section covers the case-sensitivity
decision specifically.

**Exact match always wins.** A target whose `(folder, title)` matches a note
exactly is chosen over any other candidate, unchanged from the original design.

**Case-insensitive fallback, rank 1.** If no note matches exactly, resolution
retries with `str.casefold()` applied to both the title and the folder-suffix
segments (real Unicode casefold, not `.lower()` — needed for e.g. Polish `Ł` and
German `ß`, where `.lower()` alone doesn't normalize correctly). `[[plan
projektu]]` resolves to a note titled `Plan projektu` when nothing is titled
exactly `plan projektu`. This is unlike Obsidian's fuzzy title matching in one
respect worth being explicit about: it's a strict two-stage fallback (exact, then
casefold), not a general fuzzy/alias search — a target with a typo beyond case
still doesn't resolve.

A casefold-only hit is reported back to the author as a `case_corrected_wikilink`
warning (alongside the existing `ambiguous_wikilink` kind) naming the note's real
title, so the link text can be fixed. Real ambiguity (multiple candidates) always
wins over the case-corrected label when both would otherwise apply — a target that
casefold-matches two case-twin notes is `ambiguous_wikilink`, not
`case_corrected_wikilink`.

`get_note(title=...)` is the one exception: it stays exact/case-sensitive always
(`allow_casefold=False`, `service.py:456`). A wikilink is free text embedded in a
note body and benefits from a forgiving fallback; `get_note(title=...)` is an
explicit API call, where a case-mismatched title returning a loud not-found is
better than a silent guess.

### Why this flipped from the original "stay case-sensitive" decision

The wikilink case-sensitivity question was first raised as a pure semantics
decision (GitHub issue #30, following #27's `LinkIndex` review) and the original
lean was to document the exact-match behavior and stop there. That changed when
the filesystem-collision limitation above surfaced: case-insensitive *uniqueness*
will likely be needed eventually regardless of what wikilink resolution does,
which removes the risk that made a resolution fallback feel unsafe. Adding the
fallback at the same time also directly fixes the original pain point — an LLM
writing `[[plan projektu]]` against a note titled `Plan projektu` used to be a hard
`BrokenWikilinkError` at save time; now it resolves, with a warning pointing at the
real casing.

**Rejected alternative: fix `check_unique` instead.** Making note-title uniqueness
itself case-insensitive was considered as the "real" fix for the filesystem
collision and rejected *for this change*, because it breaks the existing
case-only-rename flow: the rename path (`service.py:686`) calls
`check_unique(new_folder, new_title)` without excluding the note being renamed from
its own uniqueness check. Today that's fine — a case-only rename (`readme` →
`README`) is a different exact string, so the case-sensitive check reports
"unique" and the rename proceeds, with `folders.py:144`'s temp-dir move handling
the filesystem side. A naive case-insensitive `check_unique` would instead find the
row being renamed as a collision against itself and reject every case-only rename —
a regression. A real fix needs to exclude the target note's own id from the
uniqueness check (and decide what to do about any pre-existing case-twin notes) —
scoped separately, not bundled into this change.

**Scope boundary.** This document's wikilink section only changes what `[[target]]`
resolves to. It does not touch `check_unique`, `note_filepath`, or add any DB
constraint — two case-variant notes can still coexist per the case-sensitive prod
host today, and that risk is unchanged.
