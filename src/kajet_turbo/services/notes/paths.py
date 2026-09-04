from collections.abc import Iterable
from pathlib import Path

from kajet_turbo.markdown import IndexedNote
from kajet_turbo.workspace import note_filepath


def path_conflict_key(filepath: str) -> str:
    """Case-insensitive comparison key for a computed note path — collapses the collision
    a case-insensitive checkout filesystem (Windows NTFS, macOS APFS) would produce, even
    though the workspace's own git repo lives on prod's case-sensitive one. ``casefold()``
    over ``lower()`` as a conservative over-approximation of both filesystems' actual
    case-folding: it also merges strings neither NTFS nor APFS would (e.g. German ``ß``/
    ``ss``), but that only means treating a few more titles as colliding than strictly
    necessary — the safe direction for this check, not the exact one."""
    return filepath.casefold()


def note_path_conflict(
    paths: Iterable[IndexedNote],
    ws_path: str,
    folder: str,
    title: str,
    *,
    exclude_id: str | None = None,
) -> IndexedNote | None:
    """The other note whose computed file path equals (folder, title)'s target, or None.

    Checks DB rows only, not on-disk existence — two different titles can normalize to
    the same filename via ``title_to_windows_filename``, so this catches that even for a
    row whose file is missing (a ghost row). Callers that must also reject an orphan file
    with no matching row check ``Path(...).exists()`` separately.

    Compares via ``path_conflict_key`` (case-insensitive), not raw equality, so this also
    catches two titles that differ only by case — a collision on Windows/macOS checkouts
    even though prod's filesystem is case-sensitive.
    """
    target = path_conflict_key(note_filepath(ws_path, folder, title))
    for note in paths:
        if note.note_id == exclude_id:
            continue
        if path_conflict_key(note_filepath(ws_path, note.folder, note.title)) == target:
            return note
    return None


def build_path_index(paths: Iterable[IndexedNote], ws_path: str) -> dict[str, IndexedNote]:
    """Precompute every note's on-disk path once, for O(1) repeated conflict checks
    against a fixed snapshot — e.g. once per item in a save_many/move_folder batch,
    instead of an O(len(paths)) rescan (``note_path_conflict``) on every item.

    Keyed by ``path_conflict_key``, not the raw path — callers must fold their own lookup
    key the same way before calling ``.get()``/setting/deleting an entry.
    """
    return {path_conflict_key(note_filepath(ws_path, n.folder, n.title)): n for n in paths}


def conflict_message(title: str, filepath: str, conflict: IndexedNote) -> str:
    """Shared wording for every collision raised from ``note_path_conflict``, naming the
    target filename and the other note that already claims it."""
    return (
        f"'{title}' would be stored as '{Path(filepath).name}', already used by "
        f"note '{conflict.title}' in folder '{conflict.folder or 'root'}'."
    )


def find_path_collisions(
    paths: Iterable[IndexedNote], ws_path: str
) -> dict[str, list[IndexedNote]]:
    """Group notes by computed on-disk path (case-insensitively, via ``path_conflict_key``),
    keeping only groups with more than one note.

    Used to audit existing production data for collisions this fix now prevents going
    forward but cannot itself repair.
    """
    groups: dict[str, list[IndexedNote]] = {}
    for note in paths:
        key = path_conflict_key(note_filepath(ws_path, note.folder, note.title))
        groups.setdefault(key, []).append(note)
    return {path: notes for path, notes in groups.items() if len(notes) > 1}
