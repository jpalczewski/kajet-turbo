"""The canonical markdown-it content surface shared by tag and wikilink parsing.

Single source of truth for the parser config: CommonMark + GFM tables/strikethrough.
linkify is intentionally OFF — bare URLs were never autolinked (matches the previous
mistune render surface). Returns a fresh instance so callers can register their own
rules without mutating a shared object; the resulting per-module instances are safe to
share across threads because parse()/render() build per-call state.
"""

from markdown_it import MarkdownIt


def content_md() -> MarkdownIt:
    return MarkdownIt("commonmark").enable(["table", "strikethrough"])
