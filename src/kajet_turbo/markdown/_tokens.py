"""Shared token-stream utilities for the markdown package. Pure, no I/O."""

import re
from collections.abc import Iterator, Mapping
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

_NEWLINE_RE = re.compile(r"\r\n?|\n")


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
