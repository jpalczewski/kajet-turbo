"""Pure, deterministic markdown processing built on the markdown-it-py token stream.

No I/O, no DB, no module-level mutable state — safe under free-threaded Python.
Public API is re-exported here; private helpers live in the submodules / `_parser` / `_tokens`.
"""

from kajet_turbo.markdown.chunking import (
    DEFAULT_HARD_MAX,
    Chunk,
    chunk_markdown,
    embedded_text,
)
from kajet_turbo.markdown.note_edit import (
    AnchorAmbiguousError,
    AnchorNotFoundError,
    HeadingAmbiguousError,
    HeadingNotFoundError,
    Section,
    append_content,
    apply_edit,
    find_section_by_heading,
    insert_after,
    parse_sections,
    prepend_content,
    replace_section,
    replace_text,
)
from kajet_turbo.markdown.tags import ancestors, extract_inline_tags, normalize, segments
from kajet_turbo.markdown.wikilinks import (
    BrokenWikilinkError,
    LinkResolver,
    extract_wikilinks,
    render_markdown,
    rewrite_wikilink_target,
    split_target,
)

__all__ = [
    "DEFAULT_HARD_MAX",
    "AnchorAmbiguousError",
    "AnchorNotFoundError",
    "BrokenWikilinkError",
    "Chunk",
    "HeadingAmbiguousError",
    "HeadingNotFoundError",
    "LinkResolver",
    "Section",
    "ancestors",
    "append_content",
    "apply_edit",
    "chunk_markdown",
    "embedded_text",
    "extract_inline_tags",
    "extract_wikilinks",
    "find_section_by_heading",
    "insert_after",
    "normalize",
    "parse_sections",
    "prepend_content",
    "render_markdown",
    "replace_section",
    "replace_text",
    "rewrite_wikilink_target",
    "segments",
    "split_target",
]
