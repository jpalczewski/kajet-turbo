"""Multi-mode markdown note editing — pure string transforms, no I/O.

Ported from the Rust `kajet` MCP server (crates/parser: transforms.rs + sections.rs).
Operates on the note *body* only: in kajet-turbo the YAML frontmatter is split off by
``read_note_file()`` and re-attached by ``write_note_file()``, so transforms never see it —
which makes ``overwrite`` trivial (body = content) and ``prepend`` without a heading a plain
insert at the start of the body.

All errors subclass ``ValueError`` so existing ``except ValueError`` handlers in the service
and MCP tool catch them and surface ``{"error": ...}`` to the caller.
"""

from dataclasses import dataclass

from markdown_it import MarkdownIt

from kajet_turbo.markdown._tokens import line_offsets

# CommonMark parser (no GFM extensions — matches the Rust pulldown_cmark `Options::empty()`).
# Config-only and never mutated: `parse()` builds a fresh StateCore per call, so this shared
# instance is safe to use concurrently under free-threaded Python.
_MD = MarkdownIt("commonmark")


class HeadingNotFoundError(ValueError):
    """Raised when ``target_heading`` does not match any section."""


class HeadingAmbiguousError(ValueError):
    """Raised when ``target_heading`` matches more than one section."""


class AnchorNotFoundError(ValueError):
    """Raised when ``old_text`` is not present in the body."""


class AnchorAmbiguousError(ValueError):
    """Raised when ``old_text`` occurs more than once in the body."""


@dataclass(frozen=True)
class Section:
    """A markdown section delimited by its heading.

    ``heading_end`` is the char index just past the heading line's trailing newline.
    ``body_end`` runs to the next same-or-higher-level heading, or end of document.
    """

    level: int
    heading_text: str
    heading_start: int
    heading_end: int
    body_start: int
    body_end: int


def parse_sections(markdown: str) -> list[Section]:
    """Parse all top-level heading sections with char-accurate ranges.

    Uses a real CommonMark parser (markdown-it-py): both ATX (``## X``) and setext
    (``X\\n---``) headings are recognised, and headings inside fenced/indented code are ignored.
    Only top-level headings count as sections (``token.level == 0``) — headings nested inside
    blockquotes or list items are skipped. Each section's body extends to the next
    same-or-higher-level heading, so nested subsections belong to their parent.
    """
    tokens = _MD.parse(markdown)

    offsets = line_offsets(markdown)
    doc_len = len(markdown)

    def line_offset(line: int) -> int:
        return offsets[line] if line < len(offsets) else doc_len

    raw: list[tuple[int, int, int, str]] = []  # (level, heading_start, heading_end, heading_text)
    for i, token in enumerate(tokens):
        if token.type != "heading_open" or token.level != 0 or token.map is None:
            continue
        level = int(token.tag[1])
        heading_text = tokens[i + 1].content.strip() if i + 1 < len(tokens) else ""
        start_line, end_line = token.map
        raw.append((level, line_offset(start_line), line_offset(end_line), heading_text))

    sections: list[Section] = []
    for i, (level, h_start, h_end, h_text) in enumerate(raw):
        body_end = doc_len
        for next_level, next_start, _, _ in raw[i + 1 :]:
            if next_level <= level:
                body_end = next_start
                break
        sections.append(
            Section(
                level=level,
                heading_text=h_text,
                heading_start=h_start,
                heading_end=h_end,
                body_start=h_end,
                body_end=body_end,
            )
        )
    return sections


def find_section_by_heading(sections: list[Section], heading: str) -> Section:
    """Find a section by heading text. Accepts the heading with or without ``#`` prefix."""
    needle = heading.lstrip("#").strip()
    matches = [s for s in sections if s.heading_text.strip() == needle]
    if not matches:
        available = ", ".join(s.heading_text for s in sections)
        raise HeadingNotFoundError(f"Nagłówek nie znaleziony. Dostępne: {available}")
    if len(matches) > 1:
        raise HeadingAmbiguousError(f"Nagłówek niejednoznaczny: {len(matches)} dopasowań.")
    return matches[0]


def _find_all(content: str, needle: str) -> list[int]:
    """Non-overlapping match positions of ``needle`` in ``content``."""
    positions: list[int] = []
    start = 0
    step = max(len(needle), 1)
    while True:
        i = content.find(needle, start)
        if i == -1:
            break
        positions.append(i)
        start = i + step
    return positions


def _format_ambiguous(content: str, needle: str, positions: list[int]) -> str:
    """Render a diagnostic message listing each match's line, column and surrounding context."""
    lines = [f"Niejednoznaczne: {len(positions)} dopasowań:"]
    for pos in positions:
        before = content[:pos]
        line = before.count("\n") + 1
        last_nl = before.rfind("\n")
        col_start = last_nl + 1 if last_nl != -1 else 0
        column = len(content[col_start:pos]) + 1
        ctx_start = max(0, pos - 20)
        ctx_end = min(len(content), pos + len(needle) + 20)
        context = content[ctx_start:ctx_end].replace("\n", "\\n")
        lines.append(f"  linia {line}, kol {column}: ...{context}...")
    return "\n".join(lines)


def append_content(content: str, new_text: str, heading: str | None) -> str:
    """Append ``new_text`` at end of body, or at the end of ``heading``'s section."""
    if heading is None:
        result = content
        if not result.endswith("\n"):
            result += "\n"
        result += new_text
        if not result.endswith("\n"):
            result += "\n"
        return result

    section = find_section_by_heading(parse_sections(content), heading)
    body = content[section.body_start : section.body_end]
    content_end = section.body_start + len(body.rstrip())
    result = content[:content_end]
    if not result.endswith("\n"):
        result += "\n"
    result += new_text
    if not result.endswith("\n"):
        result += "\n"
    remainder = content[section.body_end :]
    if remainder:
        result += "\n"
    result += remainder
    return result


def prepend_content(content: str, new_text: str, heading: str | None) -> str:
    """Prepend ``new_text`` at the start of body, or right after ``heading``'s line."""
    if heading is None:
        result = new_text
        if not result.endswith("\n"):
            result += "\n"
        body_trimmed = content.lstrip("\n")
        if body_trimmed:
            result += body_trimmed
            if not result.endswith("\n"):
                result += "\n"
        return result

    section = find_section_by_heading(parse_sections(content), heading)
    insert_pos = section.heading_end
    result = content[:insert_pos]
    if not result.endswith("\n"):
        result += "\n"
    result += new_text
    if not result.endswith("\n"):
        result += "\n"
    result += content[insert_pos:]
    return result


def replace_section(content: str, heading: str, new_text: str) -> str:
    """Replace a section's body, preserving the heading line and following sections.

    If ``new_text`` opens with the same heading (a common mistake), it is stripped to avoid
    duplicating it.
    """
    section = find_section_by_heading(parse_sections(content), heading)

    body_only = new_text
    nl = new_text.find("\n")
    if nl != -1:
        first_line = new_text[:nl].lstrip()
        if first_line.startswith("#") and (
            first_line.lstrip("#").strip() == heading.lstrip("#").strip()
        ):
            body_only = new_text[nl + 1 :]

    result = content[: section.heading_end]
    if not result.endswith("\n"):
        result += "\n"
    result += body_only
    if not result.endswith("\n"):
        result += "\n"
    remainder = content[section.body_end :]
    if remainder:
        result += "\n"
    result += remainder
    return result


def replace_text(content: str, old: str, new: str) -> str:
    """Replace an exact, unique occurrence of ``old`` with ``new``. Errors on 0 or 2+ matches."""
    positions = _find_all(content, old)
    if not positions:
        raise AnchorNotFoundError("Tekst nie znaleziony.")
    if len(positions) > 1:
        raise AnchorAmbiguousError(_format_ambiguous(content, old, positions))
    pos = positions[0]
    return content[:pos] + new + content[pos + len(old) :]


def insert_after(content: str, anchor: str, new_text: str) -> str:
    """Insert ``new_text`` immediately after a unique ``anchor``. Errors on 0 or 2+ matches."""
    positions = _find_all(content, anchor)
    if not positions:
        raise AnchorNotFoundError("Tekst nie znaleziony.")
    if len(positions) > 1:
        raise AnchorAmbiguousError(_format_ambiguous(content, anchor, positions))
    pos = positions[0] + len(anchor)
    result = content[:pos]
    if not result.endswith("\n") and not new_text.startswith("\n"):
        result += "\n"
    result += new_text
    if not result.endswith("\n") and not content[pos:].startswith("\n"):
        result += "\n"
    result += content[pos:]
    return result


def apply_edit(
    body: str,
    mode: str,
    content: str,
    target_heading: str | None,
    old_text: str | None,
) -> str:
    """Dispatch to the transform for ``mode``, validating its required parameters.

    ``body`` is the current note body (no frontmatter); ``content`` is the edit payload.
    Returns the new body. Raises ``ValueError`` (or a subclass) on invalid params or failed
    anchor/heading lookups.
    """
    if mode == "overwrite" and target_heading is not None:
        raise ValueError("Tryb 'overwrite' nie używa target_heading.")
    if mode == "replace_section" and not target_heading:
        raise ValueError("Tryb 'replace_section' wymaga target_heading.")
    if mode in ("replace_text", "insert_after") and not old_text:
        raise ValueError(f"Tryb '{mode}' wymaga old_text.")
    if not content and mode != "replace_text":
        raise ValueError(f"content nie może być pusty dla trybu '{mode}'.")

    if mode == "overwrite":
        return content
    if mode == "append":
        return append_content(body, content, target_heading)
    if mode == "prepend":
        return prepend_content(body, content, target_heading)
    if mode == "replace_section":
        # target_heading guaranteed non-None by validation above.
        return replace_section(body, target_heading, content)  # ty: ignore[invalid-argument-type]
    if mode == "replace_text":
        return replace_text(body, old_text, content)  # ty: ignore[invalid-argument-type]
    if mode == "insert_after":
        return insert_after(body, old_text, content)  # ty: ignore[invalid-argument-type]
    raise ValueError(f"Nieznany tryb edycji: '{mode}'.")
