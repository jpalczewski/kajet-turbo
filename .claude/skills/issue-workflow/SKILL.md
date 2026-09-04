---
name: issue-workflow
description: Create and label GitHub issues (including epics) for kajet-turbo following the project's established conventions — title prefix, body template by type, label mapping, project board membership, native blocked-by links, epic sub-issues and tracking order, anonymization before publish. Use when filing a new issue, spinning off a follow-up from code review, or creating an epic to track a milestone's execution order.
---

GitHub issues are the only backlog for kajet-turbo — nothing gets mirrored into the kajet notebook. Issues stay open once filed; don't close-with-a-redirect-comment.

## Title

Every title starts with a prefix — this is the canonical type signal; labels are derived from it, never hand-picked independently.

- `fix:` — current behavior is wrong
- `feat:` — new capability
- `perf:` — performance work, no behavior change
- `refactor:` — cleanup/restructuring, no behavior change
- `epic:` — tracking issue for a milestone's execution order (see Epics)
- `risk:` — a latent problem, not yet triggered, not necessarily asking for action
- `investigate:` — unexplained behavior that needs root-causing before a fix can be scoped
- `Decide:` — an open design question with real options, no proposal yet
- `hardening:` — defensive improvement, not a response to an active bug
- `ci:` — CI/build pipeline
- `test:` — test coverage/quality issue

If none fit, ask rather than inventing a new prefix.

## Labels

Apply exactly one type label, mapped from the prefix: `fix`→🐛 bug, `feat`→✨ enhancement, `perf`→⚡ perf, `refactor`→♻️ refactor, `epic`→🧭 epic, `hardening`→✨ enhancement, `risk`→🐛 bug. `investigate:`/`Decide:`/`test:` issues get ❓ question unless a behavior-change label obviously fits better.

Additional labels only when unambiguous — never guess:
- `🎨 area: frontend` / `🌐 area: api` / `🔧 area: mcp` — issue is scoped to one surface
- `🔒 security` — security-relevant
- `🚧 blocked` — blocked on another open issue or an external dependency; always pair with a native blocked-by link (see Blocking relationships), the label alone doesn't say *by what*
- `🟢 priority: low` — the only priority tier that exists as a repo label; flags "known low-value, don't prioritize." There is no medium/high/critical label — cross-issue prioritization happens on the project board's `Priority` field (P0–P3) or an epic's `## Order`, not a repo label.

Most issues in this repo carry only their type label and nothing else — that's normal, not incomplete.

## Project board

Every new issue goes on the "Kajet Turbo" project board (id `PVT_kwHOAEkkts4Bhu7F`, number 11): `gh issue create ... --project "Kajet Turbo"` at creation, `gh issue edit <N> --add-project "Kajet Turbo"` for one filed without it (note the flag name differs between the two subcommands). Idempotent — safe to run even if the project has an auto-add workflow that already caught it. Needs the `project` auth scope; if it fails with a scope error, `gh auth refresh -s project`.

`Done` is set automatically if the project has a closed-issue workflow configured (it does, as of writing) — never set it by hand. Leave `Priority` (P0–P3) alone unless you have a real reason to set it; most issues carry none, and that's the norm, not neglect. Move `Status` forward through `Backlog` (scoped, ready to pick up) → `Next` (queued) → `In Progress` (branch/PR exists) as work actually happens:

```bash
item_id=$(gh project item-list 11 --owner jpalczewski --format json --limit 200 \
  --jq '.items[] | select(.content.number==<N>) | .id')
gh project item-edit --id "$item_id" --project-id PVT_kwHOAEkkts4Bhu7F \
  --field-id PVTSSF_lAHOAEkkts4Bhu7Fzhgpl3s --single-select-option-id <option-id>
```
Status option ids: Inbox `f75ad846`, Backlog `9350a0ef`, Next `f077e336`, In Progress `47fc9ee4`, Done `98236657` (don't set this last one by hand, see above).

## Blocking relationships

When issue A can't be worked until issue B lands, link them natively, not just in prose: `gh issue edit A --add-blocked-by B` (or `--blocked-by B` at creation with `gh issue create`). This is a real GitHub relationship — it shows in both issues' sidebars, is filterable (`gh issue list --search "is:blocked"`, `"blocked-by:B"`), and is queryable (`gh issue view A --json blockedBy,blocking`) — unlike a body sentence saying "blocked on #N" which nothing tracks. Add the `🚧 blocked` label to A alongside it so `gh issue list --label blocked` still works as a quick filter, and remove the label from A once every blocker it names has closed (the native link stops showing as blocking automatically; the label doesn't follow on its own).

## Body — regular issue

```
## Problem
<what's wrong / missing, with file:line references>

## Impact
<who/what is actually affected, how badly — say so plainly if it's cosmetic>

## Proposal
<the fix, briefly>

## Acceptance
- [ ] <testable criterion>
- [ ] <testable criterion>
```

`risk:` / `investigate:` / `Decide:` issues replace `## Proposal` with `## Options` — a real question gets a menu, not a foregone proposal.

If the issue is a spin-off from code review or another issue, end the body with a one-line provenance trailer: `Found during #N code review.` or `Found during review of <feature> (PR #N).`

## Body — epic

Title: `epic: <what it tracks>` (the `— execution order` suffix is optional, only #152 uses it — don't mandate it).

````
Tracking issue for the *<Milestone>* milestone. Decisions and contracts live in the linked issues; this one holds order and what blocks what.

## Order
- [ ] #N — <one-line summary of what it does/decides>
- [ ] #M — <...>

## Dependencies
```
#N ──► #M ──► ...
```
<narrative for anything that isn't a straight chain>

## Done when
<the concrete end state — what it looks like working, not "all boxes checked">
````

Check a box and append `(#PR)` once the linked issue's PR merges. Note absorption inline (`Absorbs #146.`) when one issue's work folds into another's PR instead of landing separately.

Every real issue in `## Order` is also a native GitHub sub-issue of the epic: `gh issue edit <epic> --add-sub-issue <N>` for an existing issue, or `gh issue create --parent <epic> ...` when filing a new one straight into the epic. This gets the epic a native progress bar and puts a "tracked by" breadcrumb on each child — but it doesn't carry ordering or dependency semantics, so `## Order`/`## Dependencies` in the epic body stays the authoritative sequencing; back the `──►` arrows with real `--add-blocked-by` links between the sub-issues too (see Blocking relationships). Placeholder items that aren't real issues yet (e.g. `#156`'s `E0`–`E5` letter codes) get promoted to a real sub-issue — replacing the letter code with `#N` in `## Order` — only once picked up, not upfront.

If a matching GitHub Milestone exists (`gh api repos/{owner}/{repo}/milestones`), attach the epic *and* every issue it tracks to it with `gh issue edit N --milestone "<name>"`. If the epic is new and none matches, create one first: `gh api repos/{owner}/{repo}/milestones -f title="<Milestone>" -f description="<one line>"`.

## Cross-linking

- PRs close issues via `Closes #N` lines at the top of the PR body — rely on GitHub's auto-close on merge for issues that get a PR.
- `Decide:` / `investigate:` issues usually don't get their own PR — close them by hand with a comment stating the outcome, linking the issue that carries the actual implementation if any (e.g. `#47` closed by decision, removal landed in `#89`).
- A review finding that's out of scope for the current PR gets filed as a new issue, not fixed inline — reference it in the PR body ("found but filed separately") and in the new issue's provenance trailer.

## Before publishing

If the body touches production data (real note content, real queries, names), anonymize per CLAUDE.local.md before `gh issue create` / `gh issue edit`. After creating, `gh issue view N --json body` and read the full published body back — grep is not enough for this rule.

## Branch naming

For the PR that closes the issue: `feat/...`, `fix/...`, `perf/...`, `refactor/...` — matches the issue-title prefix.
