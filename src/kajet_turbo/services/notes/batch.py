"""Shared validation vocabulary for destructive note batches (edit_many, delete_many) —
#388. Free functions, not a class: the check touches only its own parameters, never a
service's collaborators.
"""

from collections.abc import Iterator
from dataclasses import dataclass

from kajet_turbo.services.notes.staleness import sha_is_fresh, stale_error
from kajet_turbo.workspace import LocatedNote


@dataclass(frozen=True, slots=True)
class _ValidatedDestructiveItem:
    """A batch item that passed the validation shared by edits and deletes.

    Carries only what the shared check produces — the caller recovers its own richer
    per-item data (edit payload, tags, ...) via ``index`` into its original input list."""

    index: int
    note_id: str
    loc: LocatedNote


@dataclass(frozen=True, slots=True)
class _BatchValidationError:
    """A public-shaped validation error, kept typed while flowing through a batch."""

    index: int
    note_id: str
    error: str

    def as_dict(self) -> dict:
        return {"index": self.index, "note_id": self.note_id, "error": self.error}


def _validate_destructive_items(
    note_ids: list[str],
    expected_shas: list[str],
    located: dict[str, LocatedNote],
) -> Iterator[_ValidatedDestructiveItem | _BatchValidationError]:
    """Yield shared validation results in input order for batch writes.

    Keeping this as a stream lets edit_many add its edit-specific validation
    immediately, preserving the existing order of mixed validation errors.
    """
    seen_ids: set[str] = set()
    for index, (note_id, expected_sha) in enumerate(zip(note_ids, expected_shas, strict=True)):
        if not note_id:
            yield _BatchValidationError(index, note_id, "note_id is required.")
            continue
        if note_id in seen_ids:
            yield _BatchValidationError(index, note_id, f"Duplicate note_id in batch: '{note_id}'.")
            continue
        seen_ids.add(note_id)
        loc = located.get(note_id)
        if loc is None:
            yield _BatchValidationError(index, note_id, f"Note not found: note_id={note_id}")
            continue
        if not loc.file_exists:
            yield _BatchValidationError(index, note_id, f"Note file not found: note_id={note_id}")
            continue
        stripped_sha = expected_sha.strip()
        if not stripped_sha:
            yield _BatchValidationError(index, note_id, "expected_sha is required.")
            continue
        if not sha_is_fresh(loc.head_sha, stripped_sha):
            yield _BatchValidationError(index, note_id, stale_error(note_id))
            continue
        yield _ValidatedDestructiveItem(index, note_id, loc)
