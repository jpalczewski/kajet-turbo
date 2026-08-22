"""Wikilinks ``[[folder/title|alias]]`` — single markdown-it-py plugin shared by
validation and rendering, so the syntax has exactly one definition.

The inline rule only fires in inline context, so ``[[...]]`` inside code spans and
fenced/indented code blocks is ignored automatically — no manual code-range exclusion.

- ``extract_wikilinks(body)`` walks the token tree (validation: resolve targets, reject broken).
- ``render_markdown(content, resolver, slug, xws_resolver)`` renders to (unsanitized) HTML; the
  wikilink render rule resolves intra-workspace targets via ``resolver`` and cross-workspace
  ``[[note:ID]]`` links via ``xws_resolver``, both passed through ``env``
  (per-render, no module-level mutable state — safe under free-threaded Python).

This module only knows the *syntax*; what a target means (suffix matching, ambiguity) is
defined once in ``kajet_turbo.markdown.link_index`` and reaches the renderer as ``resolver``.

``BrokenWikilinkError`` subclasses ``ValueError`` so existing ``except ValueError`` handlers in
the service and API/MCP layers surface ``{"error": ...}`` to the caller.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token

from kajet_turbo.markdown._parser import content_md
from kajet_turbo.markdown._tokens import extract_meta
from kajet_turbo.workspace import path_segments


@dataclass(frozen=True, slots=True)
class IndexedNote:
    """A note's identity as seen by link resolution: where it lives and what it's called."""

    note_id: str
    folder: str
    title: str


# raw target -> the note it resolves to | None
type LinkResolver = Callable[[str], IndexedNote | None]

# note_id -> (title, url) | None
type XwsResolver = Callable[[str], tuple[str, str] | None]

XWS_PREFIX = "note:"


def xws_note_id(target: str) -> str | None:
    """The note id of a cross-workspace ``[[note:ID]]`` target, ``None`` for path targets."""
    return target.removeprefix(XWS_PREFIX) if target.startswith(XWS_PREFIX) else None


def note_explorer_url(slug: str, folder: str, note_id: str) -> str:
    """Explorer route ``/workspace/{slug}/notes/{folder…}/{id}`` — opens the note with its
    folder expanded in the tree, rather than the standalone note page."""
    segments = [quote(s) for s in path_segments(folder)] + [note_id]
    return f"/workspace/{slug}/notes/{'/'.join(segments)}"


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
    raw_alias: str | None = meta["alias"]

    if (note_id := xws_note_id(target)) is not None:
        xws_resolver: XwsResolver | None = env.get("xws_resolver")
        if xws_resolver:
            resolved = xws_resolver(note_id)
            if resolved:
                title, url = resolved
                label = escapeHtml(raw_alias or title)
                return f'<a class="wikilink xws-wikilink" href="{escapeHtml(url)}">{label}</a>'
        fallback = escapeHtml(raw_alias or note_id)
        return f'<span class="wikilink-broken">{fallback}</span>'

    label = escapeHtml(raw_alias or target)
    resolver: LinkResolver | None = env.get("wl_resolver")
    slug: str | None = env.get("wl_slug")
    resolved = resolver(target) if resolver else None
    if resolved and slug:
        # The folder comes from the resolved note, not the link text — a short [[Title]]
        # link carries no folder of its own.
        href = note_explorer_url(slug, resolved.folder, resolved.note_id)
        return f'<a class="wikilink" href="{href}">{label}</a>'
    return f'<span class="wikilink-broken">{label}</span>'


def wikilink_plugin(md: MarkdownIt) -> None:
    # Before `link` so `[[` wins over a plain `[` link opener.
    md.inline.ruler.before("link", "wikilink", _wikilink_rule)
    md.add_render_rule("wikilink", _render_wikilink)


# Config-only shared instance. CommonMark + GFM tables/strikethrough (matches the previous
# mistune render surface; linkify is intentionally left off — bare URLs were never autolinked).
# `parse()`/`render()` build fresh per-call state, so this is safe to share concurrently.
_MD = content_md()
_MD.use(wikilink_plugin)


def extract_wikilinks(body: str) -> list[tuple[str, str | None]]:
    """Return ``[(target, alias)]`` for every wikilink in ``body`` (code spans/blocks excluded)."""
    return [(meta["target"], meta["alias"]) for meta in extract_meta(_MD, body, "wikilink")]


def render_markdown(
    content: str,
    resolver: LinkResolver | None = None,
    slug: str | None = None,
    xws_resolver: XwsResolver | None = None,
) -> str:
    """Render markdown to (unsanitized) HTML. Caller must sanitize (bleach).

    ``xws_resolver`` is called for ``[[note:ID]]`` cross-workspace links to resolve an ID to
    ``(title, url)``; when absent or returning ``None`` the link renders as a broken span.
    """
    return _MD.render(
        content,
        env={"wl_resolver": resolver, "wl_slug": slug, "xws_resolver": xws_resolver},
    )


_REWRITE_RE = re.compile(r"\[\[([^\]]*?)\]\]")

# stripped target -> replacement target, or None / the same target to leave the link alone
type TargetRewriter = Callable[[str], str | None]


def rewrite_wikilinks(body: str, rewrite: TargetRewriter) -> tuple[str, bool]:
    """Replace wikilink targets ``rewrite`` maps to a new value (alias preserved). Used to keep
    backlinks valid when a note is moved/renamed; deciding *which* links point at the moved
    note is the caller's job (it has the resolution index, this function has only text).

    Operates on raw text; a ``[[...]]`` that merely *looks* like the moved note but sits inside
    a code span would also be rewritten — an accepted cosmetic edge.
    """
    changed = False

    def repl(match: re.Match[str]) -> str:
        nonlocal changed
        inner = match.group(1)
        if "[" in inner or "\n" in inner:
            return match.group(0)
        target, _, alias = inner.partition("|")
        target = target.strip()
        new_target = rewrite(target) if target else None
        if new_target is None or new_target == target:
            return match.group(0)
        changed = True
        alias = alias.strip()
        return f"[[{new_target}|{alias}]]" if alias else f"[[{new_target}]]"

    return _REWRITE_RE.sub(repl, body), changed
