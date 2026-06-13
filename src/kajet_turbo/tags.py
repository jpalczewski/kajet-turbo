"""Tag parsing: hierarchical slash-paths from frontmatter and inline ``#hashtags``.

Paths are stored bare (no leading ``#``) and lowercased, so ``#Work`` and ``work``
unify. Hierarchy is encoded in the path string; ancestors are derived by splitting.
Inline extraction reuses markdown-it tokenization so ``#tag`` inside code spans and
fenced/indented code blocks is ignored automatically (same trick as wikilinks).
"""

import re

# A normalized path is one or more segments of word chars / hyphen, slash-separated.
# ``\w`` is Unicode-aware for str patterns, so diacritics ("zażółć") are valid.
_PATH_RE = re.compile(r"^[\w-]+(?:/[\w-]+)*$")


def normalize(raw: str) -> str | None:
    """Return the canonical bare path for a raw tag, or ``None`` if invalid/empty.

    Strips a leading ``#``, drops empty segments, lowercases. Rejects (returns
    ``None``) anything containing characters outside ``[\\w-/]`` — e.g. spaces.
    """
    raw = raw.strip().lstrip("#").strip()
    segs = [s for s in raw.split("/") if s]
    if not segs:
        return None
    path = "/".join(segs).lower()
    return path if _PATH_RE.match(path) else None


def segments(path: str) -> list[str]:
    """Split a normalized path into its segment list."""
    return [s for s in path.split("/") if s]


def ancestors(path: str) -> list[str]:
    """Top-down ancestor chain including ``path`` itself.

    ``"a/b/c"`` -> ``["a", "a/b", "a/b/c"]``.
    """
    segs = segments(path)
    return ["/".join(segs[: i + 1]) for i in range(len(segs))]
