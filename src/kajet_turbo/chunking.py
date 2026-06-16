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


@dataclass
class _Section:
    header_path: list[str]
    content: str
    char_start: int
    char_end: int


def _line_offsets(text: str) -> list[int]:
    """offsets[i] = char index where source line i begins; offsets[len(lines)] = len(text)."""
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _label(level: int, text: str) -> str:
    return f"{'#' * level} {text}"


def _build_sections(text: str, title: str | None) -> list[_Section]:
    headings = _extract_headings(text)
    offsets = _line_offsets(text)
    n_lines = len(offsets) - 1
    title_entry = _label(1, title) if title else None

    def path_with_title(stack_labels: list[str]) -> list[str]:
        if title_entry and not (stack_labels and stack_labels[0].startswith("# ")):
            return [title_entry, *stack_labels]
        return list(stack_labels)

    sections: list[_Section] = []

    # Preamble: content before the first heading (or the whole doc if no headings).
    first_body_start = headings[0].open_line if headings else n_lines
    if first_body_start > 0:
        cs, ce = offsets[0], offsets[first_body_start]
        body = text[cs:ce]
        if body.strip():
            sections.append(_Section(path_with_title([]), body, cs, ce))

    # One section per heading: body spans from the heading's body_line to the next heading.
    stack: list[tuple[int, str]] = []  # (level, label)
    for idx, h in enumerate(headings):
        while stack and stack[-1][0] >= h.level:
            stack.pop()
        stack.append((h.level, _label(h.level, h.text)))
        body_end_line = headings[idx + 1].open_line if idx + 1 < len(headings) else n_lines
        cs, ce = offsets[h.body_line], offsets[body_end_line]
        body = text[cs:ce]
        sections.append(_Section(path_with_title([lbl for _, lbl in stack]), body, cs, ce))

    return sections


def embedded_text(chunk: Chunk) -> str:
    """The exact text sent to the embedder: breadcrumb lines, blank line, then body."""
    if not chunk.header_path:
        return chunk.content
    return "\n".join(chunk.header_path) + "\n\n" + chunk.content
