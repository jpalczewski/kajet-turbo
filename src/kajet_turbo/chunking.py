"""Structure-aware markdown chunking on the markdown-it-py token stream.

Pure and deterministic: no DB, no network, no module-level mutable state — safe to
call from threads under free-threaded Python. Each note splits into header-breadcrumb
chunks; the breadcrumb is recombined with the body only at embed time (``embedded_text``),
never stored.
"""

from dataclasses import dataclass

from markdown_it import MarkdownIt


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    header_path: list[str]
    content: str
    char_start: int
    char_end: int


DEFAULT_TARGET = 1400
DEFAULT_HARD_MAX = 2000
DEFAULT_MIN = 200

_MD = MarkdownIt()  # default preset: headings, fences, tables, lists


@dataclass(frozen=True)
class _Heading:
    open_line: int  # 0-based source line where the heading starts
    body_line: int  # first line after the heading (map[1])
    level: int  # 1..6
    text: str


def _extract_headings(text: str) -> list[_Heading]:
    """Headings in source order. Code-fence ``#`` lines are not headings (markdown-it
    never emits heading_open inside a fence), so no manual exclusion is needed."""
    tokens = _MD.parse(text)
    out: list[_Heading] = []
    for i, tok in enumerate(tokens):
        if tok.type != "heading_open" or tok.map is None:
            continue
        inline = tokens[i + 1]
        out.append(
            _Heading(
                open_line=tok.map[0],
                body_line=tok.map[1],
                level=int(tok.tag[1]),
                text=inline.content.strip(),
            )
        )
    return out


def embedded_text(chunk: Chunk) -> str:
    """The exact text sent to the embedder: breadcrumb lines, blank line, then body."""
    if not chunk.header_path:
        return chunk.content
    return "\n".join(chunk.header_path) + "\n\n" + chunk.content
