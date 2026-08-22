"""Obsidian-style wikilink target resolution over a workspace's notes.

A wikilink target is a *path suffix*, not a full path: ``[[Title]]`` matches any note
titled ``Title`` anywhere in the workspace, ``[[Sub/Title]]`` any note titled ``Title``
whose folder is ``Sub`` or ends with ``/Sub``. When several notes match, the winner is
picked deterministically (see ``LinkIndex.resolve``), so the same link always resolves
the same way across validation, rendering, reindexing and dangling-link healing — this
module is the single definition of what a target *means*; ``wikilinks`` defines only the
syntax.

Pure and DB-free: callers load the workspace's ``(note_id, folder, title)`` rows once and
build a ``LinkIndex`` from them (a single user's workspace is small enough to index in
memory per operation).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from kajet_turbo.markdown.wikilinks import IndexedNote, extract_wikilinks, xws_note_id
from kajet_turbo.workspace import normalize_folder, path_segments


def split_target(target: str) -> tuple[str, str]:
    """``"A/B/Title"`` -> ``("A/B", "Title")``; ``"Title"`` -> ``("", "Title")``.

    Folder is normalized the same way as note storage so a link matches the stored note's
    ``(folder, title)`` natural key. Invalid paths (e.g. ``../relative``) return the raw
    folder string — it can't match any stored note and is treated as a broken link.
    """
    target = target.strip().strip("/")
    folder_part, _, title = target.rpartition("/")
    try:
        return normalize_folder(folder_part), title.strip()
    except ValueError:
        return folder_part, title.strip()


def join_target(folder: str, title: str) -> str:
    """Inverse of ``split_target`` for a full path: ``("A/B", "T")`` -> ``"A/B/T"``."""
    return f"{folder}/{title}" if folder else title


def _shared_depth(a: list[str], b: list[str]) -> int:
    """Number of leading segments ``a`` and ``b`` have in common."""
    depth = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        depth += 1
    return depth


def _folder_matches(note_folder: str, suffix: str) -> bool:
    if not suffix:
        return True
    return note_folder == suffix or note_folder.endswith("/" + suffix)


class LinkIndex:
    """Resolves wikilink targets against a fixed set of notes.

    Candidates for a target are the notes whose title equals the target's last segment
    and whose folder ends with the target's folder part (any folder for a bare title).
    Among several candidates the winner is, in order:

    1. the exact full path from the workspace root — an explicit target never changes
       meaning because a same-titled note appeared elsewhere (bare ``[[T]]`` therefore
       still prefers a root-level ``T``, matching the pre-suffix behaviour);
    2. the note nearest the source: same folder, then the deepest shared ancestor;
    3. the shallowest folder, then folder path lexicographically — a stable tie-break.
    """

    def __init__(self, notes: Iterable[IndexedNote]) -> None:
        by_title: defaultdict[str, list[IndexedNote]] = defaultdict(list)
        for note in notes:
            by_title[note.title].append(note)
        self._by_title: dict[str, list[IndexedNote]] = dict(by_title)

    def resolve(self, target: str, source_folder: str = "") -> IndexedNote | None:
        folder, title = split_target(target)
        candidates = [n for n in self._by_title.get(title, ()) if _folder_matches(n.folder, folder)]
        if not candidates:
            return None
        source = path_segments(source_folder)

        def rank(note: IndexedNote) -> tuple[int, int, int, str]:
            segments = path_segments(note.folder)
            return (
                0 if note.folder == folder else 1,
                -_shared_depth(segments, source),
                len(segments),
                note.folder,
            )

        return min(candidates, key=rank)

    def shortest_target(
        self, note: IndexedNote, source_folder: str = "", min_segments: int = 1
    ) -> str:
        """The shortest path suffix that resolves to ``note`` from ``source_folder`` — how
        Obsidian writes links. ``min_segments`` keeps at least that many trailing segments
        (e.g. 2 to preserve a ``Folder/Title`` shape an author chose). Falls back to the full
        path, which is exact by construction."""
        segments = [*path_segments(note.folder), note.title]
        for length in range(max(1, min_segments), len(segments)):
            target = "/".join(segments[-length:])
            hit = self.resolve(target, source_folder)
            if hit is not None and hit.note_id == note.note_id:
                return target
        return join_target(note.folder, note.title)


@dataclass(frozen=True, slots=True)
class LinkResolution:
    """Outcome of resolving every wikilink in one note body.

    ``resolved_ids`` are intra-workspace targets that matched; ``broken`` the raw targets
    that did not (sorted, unique); ``xws_ids`` the ids from ``[[note:ID]]`` links, which
    this module cannot resolve (they live in other workspaces) and hands back to the caller.
    """

    resolved_ids: set[str]
    broken: list[str]
    xws_ids: list[str]

    @property
    def broken_pairs(self) -> list[tuple[str, str]]:
        """Broken targets as ``(folder, title)`` — the dangling-link storage key."""
        return sorted({split_target(t) for t in self.broken})


def resolve_content_links(index: LinkIndex, content: str, source_folder: str) -> LinkResolution:
    """Resolve every wikilink in ``content`` (code spans/blocks excluded) against ``index``,
    ranking ambiguous targets by proximity to ``source_folder``."""
    resolved_ids: set[str] = set()
    broken: set[str] = set()
    xws_ids: list[str] = []
    for target, _ in extract_wikilinks(content):
        if (xws_id := xws_note_id(target)) is not None:
            xws_ids.append(xws_id)
        elif (hit := index.resolve(target, source_folder)) is not None:
            resolved_ids.add(hit.note_id)
        else:
            broken.add(target)
    return LinkResolution(resolved_ids, sorted(broken), xws_ids)
