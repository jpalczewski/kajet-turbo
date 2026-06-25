"""Tag parsing: hierarchical slash-paths from frontmatter and inline ``#hashtags``.

Paths are stored bare (no leading ``#``) and lowercased, so ``#Work`` and ``work``
unify. Hierarchy is encoded in the path string; ancestors are derived by splitting.
Inline extraction reuses markdown-it tokenization so ``#tag`` inside code spans and
fenced/indented code blocks is ignored automatically (same trick as wikilinks).
"""

import re
from collections.abc import Iterator

from markdown_it.rules_inline import StateInline
from markdown_it.token import Token

from kajet_turbo.markdown._parser import content_md

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


def _walk(tokens: list[Token]) -> Iterator[Token]:
    for token in tokens:
        yield token
        if token.children:
            yield from _walk(token.children)


def extract_inline_tags(body: str) -> set[str]:
    """Return the set of normalized tag paths from ``#hashtags`` in ``body``.

    Tags inside code spans / fenced / indented code blocks are excluded because the
    inline rule never fires there (those become non-inline-parsed code tokens).
    """
    return {token.meta["tag"] for token in _walk(_TAG_MD.parse(body)) if token.type == "inline_tag"}
