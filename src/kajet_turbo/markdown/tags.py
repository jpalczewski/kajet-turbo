"""Tag parsing: hierarchical slash-paths from frontmatter and inline ``#hashtags``.

Paths are stored bare (no leading ``#``) and lowercased, so ``#Work`` and ``work``
unify. Hierarchy is encoded in the path string; ancestors are derived by splitting.
Inline extraction reuses markdown-it tokenization so ``#tag`` inside code spans and
fenced/indented code blocks is ignored automatically (same trick as wikilinks).
"""

import re
from collections.abc import Callable

from markdown_it.rules_inline import StateInline

from kajet_turbo.markdown._parser import content_md
from kajet_turbo.markdown._tokens import extract_meta
from kajet_turbo.workspace import path_segments

# A normalized path is one or more segments of word chars / hyphen, slash-separated.
# ``\w`` is Unicode-aware for str patterns, so diacritics ("zażółć") are valid.
_PATH_RE = re.compile(r"^[\w-]+(?:/[\w-]+)*$")

# Tag paths and folder paths share one segment grammar: non-empty, slash-separated.
segments = path_segments


def normalize(raw: str) -> str | None:
    """Return the canonical bare path for a raw tag, or ``None`` if invalid/empty.

    Strips a leading ``#``, drops empty segments, lowercases. Rejects (returns
    ``None``) anything containing characters outside ``[\\w-/]`` — e.g. spaces.
    """
    segs = segments(raw.strip().lstrip("#").strip())
    if not segs:
        return None
    path = "/".join(segs).lower()
    return path if _PATH_RE.match(path) else None


def ancestors(path: str) -> list[str]:
    """Top-down ancestor chain including ``path`` itself.

    ``"a/b/c"`` -> ``["a", "a/b", "a/b/c"]``.
    """
    segs = segments(path)
    return ["/".join(segs[: i + 1]) for i in range(len(segs))]


def remap_path(path: str, old: str, new: str) -> str | None:
    """Rename ``old`` to ``new`` within ``path``, or ``None`` if ``path`` is unaffected.

    Matches on segment boundaries, the same way ``ancestors`` splits a path and the
    repository's descendant GLOB selects one: ``work`` covers ``work/projects`` but never
    ``workflow``.
    """
    if path == old:
        return new
    if path.startswith(old + "/"):
        return new + path[len(old) :]
    return None


# Chars allowed inside an inline tag body (after the leading '#').
_TAG_BODY = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-/")


# Hyphen is NOT a word char here: 'foo-#tag' yields a tag, 'foo_#tag' does not.
def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _inline_tag_rule(state: StateInline, silent: bool) -> bool:
    """Inline rule matching ``#path`` at a word boundary on a single line."""
    pos = state.pos
    if state.src[pos] != "#":
        return False
    # Require start-of-input or a non-word, non-slash char before '#'
    # so 'C#', 'a#b' and '.../#anchor' don't produce tags.
    if pos > 0:
        prev = state.src[pos - 1]
        if _is_word_char(prev) or prev == "/":
            return False
    end = pos + 1
    n = len(state.src)
    # _TAG_BODY covers ASCII tag chars; .isalnum() extends acceptance to Unicode
    # letters/digits (so '#zażółć' is captured).
    while end < n and (state.src[end] in _TAG_BODY or state.src[end].isalnum()):
        end += 1
    tag = normalize(state.src[pos + 1 : end])
    if tag is None:
        return False
    if not silent:
        token = state.push("inline_tag", "", 0)
        token.meta = {"tag": tag}
    state.pos = end
    return True


# Parse-only instance (no render rule): used solely to tokenize for extraction,
# so the rendering pipeline in wikilinks.py is unaffected. Same base config.
_TAG_MD = content_md()
_TAG_MD.inline.ruler.before("link", "inline_tag", _inline_tag_rule)


def extract_inline_tags(body: str) -> set[str]:
    """Return the set of normalized tag paths from ``#hashtags`` in ``body``.

    Tags inside code spans / fenced / indented code blocks are excluded because the
    inline rule never fires there (those become non-inline-parsed code tokens).
    """
    return {meta["tag"] for meta in extract_meta(_TAG_MD, body, "inline_tag")}


# normalized tag path -> replacement path, or None to leave the tag alone
type TagRewriter = Callable[[str], str | None]

# Same grammar as ``_inline_tag_rule``: a '#' at a word boundary (the preceding char is
# neither a word char nor '/'), followed by tag-body chars. ``\w`` is Unicode-aware, so
# "#zażółć" matches; '-' is outside ``\w`` and therefore not a boundary blocker.
_REWRITE_RE = re.compile(r"(?<![\w/])#([\w\-/]+)")


def rewrite_inline_tags(body: str, rewrite: TagRewriter) -> tuple[str, bool]:
    """Replace inline ``#hashtags`` that ``rewrite`` maps to a new path. Deciding *which*
    paths move is the caller's job (it holds the rename); this function has only text.

    Mirrors ``rewrite_wikilinks`` in scope and in its compromises. Two accepted edges:

    - Operates on raw text, so a ``#tag`` inside a code span or fence is rewritten too —
      unlike ``extract_inline_tags``, which tokenizes and skips those. Cosmetic; the
      alternative (mapping token offsets back onto the source) buys little here.
    - A rewritten tag comes back canonical: ``#Work/`` under a work -> job rename becomes
      ``#job``. Only tags that actually move are touched, so nothing else is reformatted.
    """
    changed = False

    def repl(match: re.Match[str]) -> str:
        nonlocal changed
        current = normalize(match.group(1))
        if current is None:
            return match.group(0)
        new = rewrite(current)
        if new is None or new == current:
            return match.group(0)
        changed = True
        return f"#{new}"

    return _REWRITE_RE.sub(repl, body), changed
