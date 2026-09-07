import re
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import cast

from sqlmodel import Session

from kajet_turbo.log import logger
from kajet_turbo.periods import month_of_week, parse_period_key
from kajet_turbo.repositories.git import GitRepository, workspace_write_transaction
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.notes.locator import locate_many
from kajet_turbo.services.notes.staged_change import StagedChange, commit_rows_then_tree
from kajet_turbo.services.notes.staleness import sha_is_fresh
from kajet_turbo.workspace import (
    LocatedNote,
    NoteFrontmatter,
    normalize_folder,
    note_filepath,
    read_note_file_raw,
    write_note_file,
)

_TEMPORAL_TOKEN = re.compile(
    r"(?<![0-9A-Za-z-])(?:\d{4}-\d{2}-\d{2}|\d{4}-W\d{2}|\d{4}-\d{2}|\d{4})(?![0-9A-Za-z-])"
)


class BackfillStaleError(ValueError):
    """A previewed candidate no longer matches the note's current git/db state.

    Distinct from a plain ValueError (malformed candidate shape) so the API route can
    tell "the request is wrong" (422) apart from "the request was fine but the world
    moved since preview, retry" (409) without parsing exception text.
    """


def _folder_conflicts_with_period(folder: str, period) -> bool:
    """Treat calendar-looking folder components only as corroboration, never a source."""
    parts = folder.split("/")
    years = {part for part in parts if re.fullmatch(r"\d{4}", part)}
    if years and period.key[:4] not in years:
        return True
    if period.kind == "year":
        return False
    months = {part for part in parts if re.fullmatch(r"\d{2}", part)}
    if not months:
        return False
    # A week has no month of its own (periods.py); month_of_week's naming convention
    # is the same one collections.py uses to place weekly notes, so corroboration
    # against a folder's {month} component has to go through it too.
    month_key = month_of_week(period).key[5:7] if period.kind == "week" else period.key[5:7]
    return month_key not in months


def _classify_temporal_note(
    note_id: str, title: str, folder: str, occurred_at: str | None, period_value: str | None
) -> tuple[str, dict]:
    """Classify one note for temporal backfill.

    Returns ``(kind, payload)`` where ``kind`` is ``"candidate"``, ``"ambiguous"``, or
    ``"skipped"``. ``payload`` never carries a ``sha`` — callers attach it once they've
    resolved it (batched for a full-workspace preview, or already known for a specific
    note being re-validated at apply time).
    """
    if occurred_at is not None or period_value is not None:
        return "skipped", {"note_id": note_id, "reason": "already has temporal metadata"}
    matches = _TEMPORAL_TOKEN.findall(title)
    if len(matches) != 1:
        if matches:
            return "ambiguous", {
                "note_id": note_id,
                "title": title,
                "folder": folder,
                "reason": "title contains multiple temporal values",
            }
        return "skipped", {"note_id": note_id, "reason": "title has no canonical temporal value"}
    try:
        period = parse_period_key(matches[0])
    except ValueError:
        return "skipped", {"note_id": note_id, "reason": "title temporal value is invalid"}
    if _folder_conflicts_with_period(folder, period):
        return "ambiguous", {
            "note_id": note_id,
            "title": title,
            "folder": folder,
            "reason": "folder date conflicts with title",
        }
    field = "occurred_at" if period.kind == "day" else "period"
    return "candidate", {
        "note_id": note_id,
        "title": title,
        "folder": folder,
        "field": field,
        "value": period.key,
    }


@dataclass(frozen=True, slots=True)
class _PreparedBackfill:
    """One note validated for ``apply_temporal_backfill``, ready for the atomic write."""

    loc: LocatedNote
    meta: NoteFrontmatter
    content: str
    occurred_at: str | None
    period: str | None
    raw: bytes


class NoteTemporalService:
    def __init__(self, crud_repo: NoteRepository) -> None:
        self._crud_repo = crud_repo

    def entries_in(
        self, ws_name: str, owner_id: str, period: str, folder: str | None = None
    ) -> list[dict]:
        parsed = parse_period_key(period)
        normalized_folder = normalize_folder(folder) if folder is not None else None
        return self._crud_repo.entries_in(
            ws_name,
            owner_id,
            parsed.start.isoformat(),
            parsed.next().start.isoformat(),
            folder=normalized_folder,
        )

    def temporal_backfill_preview(
        self, ws_name: str, owner_id: str, ws_path: str
    ) -> dict[str, list[dict]]:
        """Find safe title-derived temporal metadata without changing any note."""
        ambiguous: list[dict] = []
        skipped: list[dict] = []
        pending: list[tuple[dict, str]] = []
        for note in self._crud_repo.list_notes(ws_name, owner_id, limit=None):
            kind, payload = _classify_temporal_note(
                note["note_id"], note["title"], note["folder"], note["occurred_at"], note["period"]
            )
            if kind == "ambiguous":
                ambiguous.append(payload)
            elif kind == "skipped":
                skipped.append(payload)
            else:
                relative = str(
                    Path(note_filepath(ws_path, note["folder"], note["title"])).relative_to(ws_path)
                )
                pending.append((payload, relative))
        # One batched git walk for every candidate's sha instead of one Repo-open-and-walk
        # per note (locate_many does the same batching for every other batch operation).
        head_shas = GitRepository(ws_path).head_shas_for_paths(
            [relative for _, relative in pending]
        )
        candidates = [{**payload, "sha": head_shas[relative]} for payload, relative in pending]
        return {"candidates": candidates, "ambiguous": ambiguous, "skipped": skipped}

    @workspace_write_transaction
    def apply_temporal_backfill(
        self, ws_name: str, owner_id: str, ws_path: str, candidates: list[dict]
    ) -> dict:
        """Apply an explicitly previewed backfill batch without touching search chunks."""
        if not candidates:
            raise ValueError("candidates must not be empty")
        requested_ids = [item.get("note_id") for item in candidates]
        if any(not isinstance(i, str) or not i for i in requested_ids):
            raise ValueError("candidates: every item needs a non-empty string note_id")
        if len(set(requested_ids)) != len(requested_ids):
            raise ValueError("candidates: note_id values must be unique")
        # One GitRepository for both the batch locate and the staged write below — a
        # second open re-parses refs/pack indexes for no reason (see locate_many's
        # docstring and edit_many/delete_many for the established one-repo-per-batch shape).
        git_repo = GitRepository(ws_path)
        located = locate_many(
            self._crud_repo, cast(list[str], requested_ids), owner_id, ws_path, git_repo
        )
        prepared: list[_PreparedBackfill] = []
        for item in candidates:
            loc = located.get(item["note_id"])
            if loc is None or not loc.file_exists:
                raise BackfillStaleError("backfill preview is stale; run preview again")
            # item["sha"] is None for a note with no matching git history yet (a brand
            # new file). sha_is_fresh treats a falsy expected_sha as never fresh, so that
            # case is compared directly instead: still-no-history is fresh, anything else
            # is a real change since preview.
            fresh = (
                loc.head_sha is None
                if item["sha"] is None
                else sha_is_fresh(loc.head_sha, item["sha"])
            )
            if not fresh:
                raise BackfillStaleError("backfill preview is stale; run preview again")
            # Re-derive the candidate from the current row instead of re-running preview
            # over the whole workspace (this method already holds the write lock; a full
            # rescan here would block every other write for O(workspace size), not
            # O(len(candidates))).
            kind, expected = _classify_temporal_note(
                loc.note.id, loc.note.title, loc.note.folder, loc.note.occurred_at, loc.note.period
            )
            if kind != "candidate" or {**expected, "sha": item["sha"]} != item:
                raise BackfillStaleError("backfill preview is stale; run preview again")
            meta, content, raw = read_note_file_raw(loc.filepath)
            occurred_at = item["value"] if item["field"] == "occurred_at" else None
            period = item["value"] if item["field"] == "period" else None
            prepared.append(
                _PreparedBackfill(
                    loc=loc,
                    meta=meta,
                    content=content,
                    occurred_at=occurred_at,
                    period=period,
                    raw=raw,
                )
            )
        items = [
            StagedChange(
                add=p.loc.relative,
                remove=None,
                apply=partial(
                    write_note_file,
                    p.loc.filepath,
                    replace(p.meta, occurred_at=p.occurred_at, period=p.period),
                    p.content,
                ),
                known_bytes=p.raw,
            )
            for p in prepared
        ]
        message = f"note: backfill temporal metadata ({len(items)} notes)"

        def write_rows(session: Session) -> None:
            for p in prepared:
                # updated_at and index_generation are deliberately left alone: this
                # rewrites frontmatter dates only, not the note's indexed text.
                self._crud_repo.update_in_session(
                    session,
                    p.loc.note.id,
                    owner_id=owner_id,
                    updated_at=p.loc.note.updated_at,
                    occurred_at=p.occurred_at,
                    period=p.period,
                    bump_index_generation=False,
                )

        commit_rows_then_tree(
            self._crud_repo,
            git_repo,
            items,
            message,
            operation="backfill_temporal_metadata",
            write_rows=write_rows,
            workspace=ws_name,
            owner_id=owner_id,
            count=len(prepared),
        )
        logger.info("temporal_backfill_applied", ws=ws_name, count=len(prepared))
        return {"applied": len(prepared)}
