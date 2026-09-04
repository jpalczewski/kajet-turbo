from dataclasses import replace

from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.workspace import LocatedNote, locate_note


def locate_many(
    repo: NoteRepository,
    note_ids: list[str],
    owner_id: str,
    ws_path: str,
    git_repo: GitRepository,
) -> dict[str, LocatedNote]:
    """Locate a note batch with one row lookup and one shared Git history walk.

    Only files that currently exist enter the history walk. A missing file has no
    head sha to resolve and including it would prevent the walk's early exit.

    Rows and file existence are read once before caller-owned validation. This shifts
    the TOCTOU window slightly relative to checking each item independently, but the
    sha-freshness check is already advisory outside the workspace write lock.
    """
    stripped_ids = [note_id for raw_id in note_ids if (note_id := raw_id.strip())]
    notes = repo.get_many(stripped_ids, owner_id)
    located = {note.id: locate_note(note, ws_path) for note in notes}
    head_shas = git_repo.head_shas_for_paths(
        [loc.relative for loc in located.values() if loc.file_exists]
    )
    return {
        note_id: replace(loc, head_sha=head_shas.get(loc.relative))
        for note_id, loc in located.items()
    }
