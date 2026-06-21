"""Structure-aware markdown chunking on the markdown-it-py token stream.

Pure and deterministic: no DB, no network, no module-level mutable state — safe to
call from threads under free-threaded Python. Each note splits into header-breadcrumb
chunks; the breadcrumb is recombined with the body only at embed time (``embedded_text``),
never stored.
"""

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt

# Intentionally crude: splits on every ". " etc., so it also breaks on abbreviations
# ("e.g. ") and decimals ("3.14 "). That's acceptable here because the only contract on
# the resulting pieces is that each fits hard_max — semantic sentence accuracy is not needed.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

_FENCE_MARKERS = ("```", "~~~")


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    header_path: list[str]
    content: str
    # Source span of the originating section(s) — provenance for locating/highlighting the
    # chunk in the original note. NOT a slice that reconstructs content: text[char_start:
    # char_end] != content in general (content drops heading lines, and after merge or
    # sub-split the span covers more than the piece).
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


def _common_prefix(a: list[str], b: list[str]) -> list[str]:
    out: list[str] = []
    for x, y in zip(a, b, strict=False):  # prefix of possibly-different-length paths
        if x != y:
            break
        out.append(x)
    return out


def _merge_small(sections: list[_Section], *, target: int, min_size: int) -> list[_Section]:
    """Greedily fold an adjacent section into the previous one when either is below
    ``min_size`` and the combined body still fits ``target``. The merged header_path
    drops to the longest common prefix of the two."""
    merged: list[_Section] = []
    for sec in sections:
        if merged:
            prev = merged[-1]
            # `target` is a merge ceiling, not a split target: a standalone section between
            # `target` and `hard_max` is emitted whole; only merges must stay under it.
            combined = len(prev.content) + len(sec.content)
            if combined <= target and (len(prev.content) < min_size or len(sec.content) < min_size):
                prev.content = prev.content.rstrip("\n") + "\n\n" + sec.content.lstrip("\n")
                prev.header_path = _common_prefix(prev.header_path, sec.header_path)
                prev.char_end = sec.char_end
                continue
        merged.append(_Section(list(sec.header_path), sec.content, sec.char_start, sec.char_end))
    return merged


def _blocks(body: str) -> list[str]:
    """Split body into blocks: fenced code blocks stay atomic; runs of non-blank lines
    are paragraphs; blank lines separate."""
    lines = body.splitlines()
    blocks: list[str] = []
    buf: list[str] = []
    fence_marker: str | None = None  # the marker char run that opened the current fence

    def flush() -> None:
        if buf:
            blocks.append("\n".join(buf))
            buf.clear()

    for line in lines:
        stripped = line.lstrip()
        # Inside a fence only a line starting with the *same* marker closes it, so a ~~~
        # line inside a ``` fence (or vice-versa) is just code, not a delimiter.
        if fence_marker is not None:
            buf.append(line)
            if stripped.startswith(fence_marker):
                flush()
                fence_marker = None
            continue
        opener = next((m for m in _FENCE_MARKERS if stripped.startswith(m)), None)
        if opener is not None:
            flush()
            fence_marker = opener
            buf.append(line)
        elif line.strip() == "":
            flush()
        else:
            buf.append(line)
    flush()
    return blocks


def _split_oversized_block(block: str, hard_max: int) -> list[str]:
    """A single block bigger than hard_max. A fence (``` or ~~~) that itself exceeds
    hard_max can't stay atomic, so it is split on line boundaries (the first piece keeps
    the opening marker, the last keeps the closer); a paragraph is split on sentences.
    Always yields pieces <= hard_max."""
    is_fence = block.lstrip().startswith(_FENCE_MARKERS)
    units = block.splitlines() if is_fence else _SENTENCE_RE.split(block)
    sep = "\n" if is_fence else " "
    pieces: list[str] = []
    cur = ""
    for unit in units:
        candidate = unit if not cur else cur + sep + unit
        if len(candidate) <= hard_max or not cur:
            cur = candidate
        else:
            pieces.append(cur)
            cur = unit
        while len(cur) > hard_max:  # a single unit longer than hard_max: hard cut
            pieces.append(cur[:hard_max])
            cur = cur[hard_max:]
    if cur:
        pieces.append(cur)
    return pieces


def _split_body(body: str, *, hard_max: int) -> list[str]:
    """Pack blocks into pieces <= hard_max. A fence is kept atomic only when it fits under
    hard_max; an oversized fence is line-split via _split_oversized_block (first piece keeps
    the opening marker, last the closer)."""
    if len(body) <= hard_max:
        return [body]
    pieces: list[str] = []
    cur = ""
    for block in _blocks(body):
        if len(block) > hard_max:
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.extend(_split_oversized_block(block, hard_max))
            continue
        candidate = block if not cur else cur + "\n\n" + block
        if len(candidate) <= hard_max:
            cur = candidate
        else:
            pieces.append(cur)
            cur = block
    if cur:
        pieces.append(cur)
    return pieces


def chunk_markdown(
    text: str,
    title: str | None = None,
    *,
    target: int = DEFAULT_TARGET,
    hard_max: int = DEFAULT_HARD_MAX,
    min_size: int = DEFAULT_MIN,
) -> list[Chunk]:
    """Split markdown into header-breadcrumb chunks. Pure and deterministic.

    char_start/char_end on each chunk mark the source span of the originating section(s)
    (provenance for highlighting); they do NOT necessarily satisfy
    text[char_start:char_end] == content, e.g. after merge or sub-split.
    """
    if not 0 <= min_size <= target <= hard_max:
        raise ValueError(
            f"sizes must satisfy 0 <= min_size <= target <= hard_max, got "
            f"min_size={min_size}, target={target}, hard_max={hard_max}"
        )
    if not text.strip():
        return []
    sections = _build_sections(text, title)
    sections = _merge_small(sections, target=target, min_size=min_size)

    chunks: list[Chunk] = []
    ordinal = 0
    for sec in sections:
        body = sec.content.strip("\n")
        if not body.strip():
            continue
        pieces = _split_body(body, hard_max=hard_max)
        for piece in pieces:
            if not piece.strip():
                continue
            chunks.append(
                Chunk(
                    ordinal=ordinal,
                    header_path=list(sec.header_path),
                    content=piece,
                    char_start=sec.char_start,
                    char_end=sec.char_end,
                )
            )
            ordinal += 1
    return chunks


def embedded_text(chunk: Chunk) -> str:
    """The exact text sent to the embedder: breadcrumb lines, blank line, then body."""
    if not chunk.header_path:
        return chunk.content
    return "\n".join(chunk.header_path) + "\n\n" + chunk.content
