"""Obsidian-style wikilink target resolution over a workspace's notes.

A wikilink target is a *path suffix*, not a full path: ``[[Title]]`` matches any note
titled ``Title`` anywhere in the workspace, ``[[Sub/Title]]`` any note titled ``Title``
whose folder is ``Sub`` or ends with ``/Sub``. When several notes match, the winner is
picked deterministically (see ``LinkIndex.resolve``), so the same link always resolves the
same way across validation, rendering, reindexing and dangling-link healing — this module
is the single definition of what a target *means*; ``wikilinks`` defines only the syntax.

Pure and DB-free: callers load the workspace's ``(note_id, folder, title)`` rows once and
build a ``LinkIndex`` from them (a single user's workspace is small enough to index in
memory per operation).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from kajet_turbo.markdown.wikilinks import IndexedNote, extract_wikilinks
from kajet_turbo.workspace import normalize_folder

XWS_PREFIX = "note:"


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


def _segments(folder: str) -> list[str]:
    return [s for s in folder.split("/") if s]


def _shared_depth(a: str, b: str) -> int:
    """Number of leading folder segments ``a`` and ``b`` have in common."""
    depth = 0
    for x, y in zip(_segments(a), _segments(b), strict=False):
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

        def rank(note: IndexedNote) -> tuple[int, int, int, str]:
            return (
                0 if note.folder == folder else 1,
                -_shared_depth(note.folder, source_folder),
                len(_segments(note.folder)),
                note.folder,
            )

        return min(candidates, key=rank)


@dataclass(frozen=True, slots=True)
class LinkResolution:
    """Outcome of resolving every wikilink in one note body.

    ``resolved_ids`` are intra-workspace targets that matched; ``broken`` the raw targets
    that did not (sorted, unique); ``xws_ids`` the ids from ``[[note:ID]]`` links, which
    this module cannot resolve (they live in other workspaces) and hands back to the caller.
    """

    resolved_ids: set[str] = field(default_factory=set)
    broken: list[str] = field(default_factory=list)
    xws_ids: list[str] = field(default_factory=list)

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
        if target.startswith(XWS_PREFIX):
            xws_ids.append(target[len(XWS_PREFIX) :])
            continue
        hit = index.resolve(target, source_folder)
        if hit is None:
            broken.add(target)
        else:
            resolved_ids.add(hit.note_id)
    return LinkResolution(resolved_ids, sorted(broken), xws_ids)
