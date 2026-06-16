"""Structure-aware markdown chunking on the markdown-it-py token stream.

Pure and deterministic: no DB, no network, no module-level mutable state — safe to
call from threads under free-threaded Python. Each note splits into header-breadcrumb
chunks; the breadcrumb is recombined with the body only at embed time (``embedded_text``),
never stored.
"""

from dataclasses import dataclass


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


def embedded_text(chunk: Chunk) -> str:
    """The exact text sent to the embedder: breadcrumb lines, blank line, then body."""
    if not chunk.header_path:
        return chunk.content
    return "\n".join(chunk.header_path) + "\n\n" + chunk.content
