"""Wikilinks ``[[folder/title|alias]]`` — single markdown-it-py plugin shared by
validation and rendering, so the syntax has exactly one definition.

The inline rule only fires in inline context, so ``[[...]]`` inside code spans and
fenced/indented code blocks is ignored automatically — no manual code-range exclusion.

- ``extract_wikilinks(body)`` walks the token tree (validation: resolve targets, reject broken).
- ``render_markdown(content, resolver, slug)`` renders to (unsanitized) HTML; the wikilink
  render rule resolves each target to a ``note_id`` via the ``resolver`` passed through ``env``
  (per-render, no module-level mutable state — safe under free-threaded Python).

``BrokenWikilinkError`` subclasses ``ValueError`` so existing ``except ValueError`` handlers in
the service and API/MCP layers surface ``{"error": ...}`` to the caller.
"""

import re
from collections.abc import Callable, Iterator
from urllib.parse import quote

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token

from kajet_turbo.workspace import normalize_folder

# (folder, title) -> note_id | None
LinkResolver = Callable[[str, str], str | None]


class BrokenWikilinkError(ValueError):
    """Raised when a note's content links to targets that don't resolve to existing notes."""

    def __init__(self, broken: list[str]) -> None:
        self.broken = broken
        listed = ", ".join(f"[[{b}]]" for b in broken)
        super().__init__(f"Niezresolwowane wikilinki: {listed}")


def _wikilink_rule(state: StateInline, silent: bool) -> bool:
    """Inline rule matching ``[[target]]`` / ``[[target|alias]]`` on a single line."""
    if not state.src.startswith("[[", state.pos):
        return False
    end = state.src.find("]]", state.pos + 2)
    if end < 0:
        return False
    inner = state.src[state.pos + 2 : end]
    # Keep wikilinks simple: no nesting, no spanning lines.
    if "[" in inner or "]" in inner or "\n" in inner:
        return False
    target, _, alias = inner.partition("|")
    if not target.strip():
        return False
    if not silent:
        token = state.push("wikilink", "", 0)
        token.meta = {"target": target.strip(), "alias": alias.strip() or None}
    state.pos = end + 2
    return True


def _render_wikilink(self, tokens: list[Token], idx: int, options, env) -> str:
    meta = tokens[idx].meta
    target: str = meta["target"]
    label = escapeHtml(meta["alias"] or target)
    resolver: LinkResolver | None = env.get("wl_resolver")
    slug: str | None = env.get("wl_slug")
    folder, title = split_target(target)
    note_id = resolver(folder, title) if resolver else None
    if note_id and slug:
        # Point at the explorer route (/notes/{folder}/{id}) so the click opens the target's
        # folder and shows the file in the tree, rather than the standalone note page.
        segments = [quote(s) for s in folder.split("/") if s] + [note_id]
        href = f"/workspace/{slug}/notes/{'/'.join(segments)}"
        return f'<a class="wikilink" href="{href}">{label}</a>'
    return f'<span class="wikilink-broken">{label}</span>'


def wikilink_plugin(md: MarkdownIt) -> None:
    # Before `link` so `[[` wins over a plain `[` link opener.
    md.inline.ruler.before("link", "wikilink", _wikilink_rule)
    md.add_render_rule("wikilink", _render_wikilink)


# Config-only shared instance. CommonMark + GFM tables/strikethrough (matches the previous
# mistune render surface; linkify is intentionally left off — bare URLs were never autolinked).
# `parse()`/`render()` build fresh per-call state, so this is safe to share concurrently.
_MD = MarkdownIt("commonmark").enable(["table", "strikethrough"])
_MD.use(wikilink_plugin)


def split_target(target: str) -> tuple[str, str]:
    """``"A/B/Title"`` -> ``("A/B", "Title")``; ``"Title"`` -> ``("", "Title")``.

    Folder is normalized the same way as note storage so a link matches the stored note's
    ``(folder, title)`` natural key.
    """
    target = target.strip().strip("/")
    folder_part, _, title = target.rpartition("/")
    return normalize_folder(folder_part), title.strip()


def _walk(tokens: list[Token]) -> Iterator[Token]:
    for token in tokens:
        yield token
        if token.children:
            yield from _walk(token.children)


def extract_wikilinks(body: str) -> list[tuple[str, str | None]]:
    """Return ``[(target, alias)]`` for every wikilink in ``body`` (code spans/blocks excluded)."""
    return [
        (token.meta["target"], token.meta["alias"])
        for token in _walk(_MD.parse(body))
        if token.type == "wikilink"
    ]


def render_markdown(
    content: str, resolver: LinkResolver | None = None, slug: str | None = None
) -> str:
    """Render markdown to (unsanitized) HTML. Caller must sanitize (bleach)."""
    return _MD.render(content, env={"wl_resolver": resolver, "wl_slug": slug})


_REWRITE_RE = re.compile(r"\[\[([^\]]*?)\]\]")


def rewrite_wikilink_target(
    body: str, old_key: tuple[str, str], new_target: str
) -> tuple[str, bool]:
    """Rewrite every wikilink whose normalized ``(folder, title)`` equals ``old_key`` to point at
    ``new_target`` (alias preserved). Used to keep backlinks valid when a note is moved/renamed.

    Matching is on the normalized key, not the raw string, so different spellings of the same
    target are all updated. Operates on raw text; a ``[[...]]`` that merely *looks* like the
    moved note but sits inside a code span would also be rewritten — an accepted cosmetic edge.
    """
    changed = False

    def repl(match: re.Match[str]) -> str:
        nonlocal changed
        inner = match.group(1)
        if "[" in inner or "\n" in inner:
            return match.group(0)
        target, _, alias = inner.partition("|")
        if not target.strip() or split_target(target) != old_key:
            return match.group(0)
        changed = True
        alias = alias.strip()
        return f"[[{new_target}|{alias}]]" if alias else f"[[{new_target}]]"

    return _REWRITE_RE.sub(repl, body), changed
