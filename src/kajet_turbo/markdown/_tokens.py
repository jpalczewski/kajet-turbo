"""Shared token-stream utilities for the markdown package. Pure, no I/O."""

from collections.abc import Iterator, Mapping
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token


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
