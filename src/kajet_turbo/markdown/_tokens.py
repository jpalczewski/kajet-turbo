"""Shared token-stream utilities for the markdown package. Pure, no I/O."""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

_NEWLINE_RE = re.compile(r"\r\n?|\n")


@dataclass(frozen=True, slots=True)
class Heading:
    level: int  # 1..6
    text: str
    open_line: int  # token.map[0], 0-based source line where the heading starts
    body_line: int  # token.map[1], first line after the heading


def iter_headings(tokens: list[Token], *, top_level_only: bool = False) -> Iterator[Heading]:
    """Yield one Heading per ``heading_open`` token with a non-None ``.map``.

    Operates on an already-parsed token stream so each caller keeps its own MarkdownIt
    preset. When ``top_level_only`` is set, headings nested in a blockquote/list
    (``token.level != 0``) are skipped.
    """
    for i, tok in enumerate(tokens):
        if tok.type != "heading_open" or tok.map is None:
            continue
        if top_level_only and tok.level != 0:
            continue
        inline = tokens[i + 1]
        yield Heading(
            level=int(tok.tag[1]),
            text=inline.content.strip(),
            open_line=tok.map[0],
            body_line=tok.map[1],
        )


def line_offsets(text: str) -> list[int]:
    """offsets[i] = char index where source line i begins; offsets[-1] == len(text).

    Line boundaries match markdown-it normalization (``\\r\\n``, bare ``\\r``, ``\\n`` — but
    NOT ``\\v``/``\\f``/U+2028/U+2029), so offsets index the original (un-normalized) text and
    align with ``token.map`` line numbers.
    """
    offsets = [0]
    for m in _NEWLINE_RE.finditer(text):
        offsets.append(m.end())
    if offsets[-1] != len(text):
        offsets.append(len(text))
    return offsets


def walk_tokens(tokens: list[Token]) -> Iterator[Token]:
    """Depth-first walk yielding every token, descending into inline children."""
    for token in tokens:
        yield token
        if token.children:
            yield from walk_tokens(token.children)


def extract_meta(md: MarkdownIt, body: str, token_type: str) -> Iterator[Mapping[str, Any]]:
    """Parse ``body`` with ``md`` and yield ``.meta`` of every token of ``token_type``.

    Tokens inside code spans / fenced / indented blocks never appear because the inline
    rules that would push them don't fire there.
    """
    return (t.meta for t in walk_tokens(md.parse(body)) if t.type == token_type)
