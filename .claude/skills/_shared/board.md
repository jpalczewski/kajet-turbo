# Kajet Turbo board — shared reference

Read by the `issue-filing`, `backlog-triage` and `next-selection` skills. This directory
has no `SKILL.md` on purpose: it is not a skill, it is what the three skills share.

## Pipeline

The "Kajet Turbo" project board (user project 11) is the only backlog. Its `Status` field
is the pipeline, and each transition has one owning skill:

| Status | Meaning | Put there by |
|---|---|---|
| `Inbox` | filed, not yet weighed against the rest | `issue-filing` |
| `Backlog` | triaged: has a priority, ready to pick up | `backlog-triage` |
| `Next` | selected for the current round of work | `next-selection` |
| `In Progress` | a branch or PR exists | whoever starts the work |
| `Blocked` | waiting on something **outside** GitHub issues (see Blocking) | whoever finds the wait |
| `Done` | closed | the project's closed-issue workflow — never set by hand |

## `board.py`

All board reads and writes go through `python3 .claude/skills/_shared/board.py`. It holds
the project, field and option ids, so nothing else needs to copy them.

- `list [--status S ...]` — open board issues sorted by status and priority, with type and
  open blockers.
- `set N [N ...] [--status S] [--priority P0|P1|P2|P3|none]` — edit Status and/or Priority.
- `stale` — read-only report of mechanical staleness (see Staleness below).
- `tick-epic N [--apply]` — tick an epic's checklist entries whose issue is closed and
  append the merged PR. It is a dry run without `--apply`. When no PR is found it leaves an
  HTML comment in place of the ref, and you replace that comment by hand.

Adding an issue to the board is `gh issue create ... --project "Kajet Turbo"`, or
`gh issue edit N --add-project "Kajet Turbo"` afterwards. If `gh` fails with a scope
error, run `gh auth refresh -s project`.

## Priority

`Priority` is a project field. It is the **only** place priority lives: there is no
priority label, and the 🟢 priority: low label was removed on 2026-09-13 because it
drifted from the field.

| P | Meaning | Typical shape |
|---|---|---|
| P0 | active harm now — drop other work | a production data or PII leak, a security hole being exercised, a broken core path |
| P1 | next up | a real risk with a small fix; dev-loop pain that hits every session; a confirmed user-visible bug; the next step of the milestone being worked |
| P2 | worth doing, no urgency | a latent risk with no incident; a real feature gap; a refactor that has a correctness angle; a middle link of a milestone chain |
| P3 | cleanup | cosmetic, dedupe, micro-perf, "not a problem today" proactive notes; the tail of a milestone chain |

- Epics carry no priority. Their `## Order` is the sequencing.
- An issue whose own Impact section says "cosmetic" or "not an active incident" is not
  upgraded without a stated reason.
- `In Progress` items do not need a priority.

## Blocking

- **Issue blocked by an issue**: use the native link only. Run
  `gh issue edit A --add-blocked-by B`, or pass `--blocked-by B` at creation. It clears
  itself when B closes and is searchable with `gh issue list --search "is:blocked"`.
  There is no blocked label: the 🚧 blocked label was removed on 2026-09-13 after four
  copies were found still set on issues whose blockers had closed.
- **Blocked by something that is not an issue** (production data volume, an upstream
  release, a decision outside the repo): set Status `Blocked` **and** add a
  `## Blocked on` section to the body with one sentence naming what the issue waits for.
  `board.py stale` flags a `Blocked` item that lacks this section.
- An issue-to-issue block keeps its normal Status (`Backlog`, or `Next` as part of an
  ordered chain). It is never moved to `Blocked`.

## Staleness

Run `board.py stale` before any triage or selection. It reports:

- open issues missing from the board;
- `Blocked` used for an issue-to-issue block, or used without a `## Blocked on` section;
- items in `Next` blocked by an open issue (fine only as an ordered chain);
- issues with an open closing PR that are not `In Progress`;
- open non-bot PRs whose title or branch names an issue with no `Closes` line;
- epics with closed children still unticked, and epics where every open checkbox is a
  closed issue (a close candidate: ask before closing);
- triaged issues without a priority, and epics with one.

The report cannot see an **obsolete premise**. Code-review spin-offs cite `file:line` and
symbols, and a later refactor moves or deletes them. `git fetch` first. Then, for every
issue you are about to prioritize or select, check its citations against `origin/main`:

| What you find | Action |
|---|---|
| cited code moved, problem intact | keep the issue; mention the new location in your proposal |
| already fixed | close as completed; the comment names the PR or commit that fixed it |
| premise gone (superseded by a refactor) | close as not planned; the comment names the superseding PR and says what changed |
| implemented by an open PR | add a `Closes #N` line at the top of the PR body; Status `In Progress` |

Closing comments are public: write them in English and apply the anonymization rules in
`CLAUDE.local.md`. Closing an epic is always a question to the user, never an automatic
step.

## Concurrency

Other sessions move items too. For example, a research session moves its issues to
`In Progress` while you are planning. Re-run `board.py list` right before applying a
batch. Never move an item that is `In Progress` unless the user asks.
