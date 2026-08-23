"""Multi-mode markdown note editing — pure string transforms, no I/O.

Ported from the Rust `kajet` MCP server (crates/parser: transforms.rs + sections.rs).
Operates on the note *body* only: in kajet-turbo the YAML frontmatter is split off by
``read_note_file()`` and re-attached by ``write_note_file()``, so transforms never see it —
which makes ``overwrite`` trivial (body = content) and ``prepend`` without a heading a plain
insert at the start of the body.

Vocabulary: ``body`` is always the haystack being edited, ``text`` the payload going into
it. The public edit payload is split the same way — whole-body modes take ``content``,
text modes take an ``old_str`` anchor and its ``new_str`` replacement — so no parameter
changes meaning depending on the mode.

All errors subclass ``ValueError`` so existing ``except ValueError`` handlers in the service
and MCP tool catch them and surface ``{"error": ...}`` to the caller. Those messages reach
the calling LLM verbatim, so they are written in English and name the parameter at fault.
"""

from collections.abc import Callable
from dataclasses import dataclass

from markdown_it import MarkdownIt

from kajet_turbo.markdown._tokens import iter_headings, line_offsets

# CommonMark parser (no GFM extensions — matches the Rust pulldown_cmark `Options::empty()`).
# Config-only and never mutated: `parse()` builds a fresh StateCore per call, so this shared
# instance is safe to use concurrently under free-threaded Python.
_MD = MarkdownIt("commonmark")


class HeadingNotFoundError(ValueError):
    """Raised when ``target_heading`` does not match any section."""


class HeadingAmbiguousError(ValueError):
    """Raised when ``target_heading`` matches more than one section."""


class AnchorNotFoundError(ValueError):
    """Raised when ``old_str`` is not present in the body."""


class AnchorAmbiguousError(ValueError):
    """Raised when ``old_str`` occurs more than once in the body."""


@dataclass(frozen=True, slots=True)
class EditResult:
    """Result of ``apply_edit``. ``replaced`` is the match count for
    replace_text/delete_text when ``replace_all=True`` was used, else ``None``."""

    body: str
    replaced: int | None = None


@dataclass(frozen=True, slots=True)
class Section:
    """A markdown section delimited by its heading.

    ``heading_end`` is the char index just past the heading line's trailing newline.
    ``body_end`` runs to the next same-or-higher-level heading, or end of document.
    ``heading_line`` is the 0-based source line the heading starts on (from the
    underlying ``Heading.open_line``) — used by ``markdown.outline`` for 1-based
    line-span reporting without re-deriving it from ``heading_start``.
    """

    level: int
    heading_text: str
    heading_start: int
    heading_end: int
    body_start: int
    body_end: int
    heading_line: int


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

    # (level, heading_start, heading_end, heading_text, heading_line)
    raw: list[tuple[int, int, int, str, int]] = []
    for h in iter_headings(tokens, top_level_only=True):
        raw.append(
            (h.level, line_offset(h.open_line), line_offset(h.body_line), h.text, h.open_line)
        )

    sections: list[Section] = []
    for i, (level, h_start, h_end, h_text, h_line) in enumerate(raw):
        body_end = doc_len
        for next_level, next_start, _, _, _ in raw[i + 1 :]:
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
                heading_line=h_line,
            )
        )
    return sections


def find_section_by_heading(sections: list[Section], heading: str) -> Section:
    """Find a section by heading text. Accepts the heading with or without ``#`` prefix."""
    needle = heading.lstrip("#").strip()
    matches = [s for s in sections if s.heading_text.strip() == needle]
    if not matches:
        available = ", ".join(s.heading_text for s in sections)
        raise HeadingNotFoundError(f"Heading not found. Available: {available}")
    if len(matches) > 1:
        raise HeadingAmbiguousError(f"Heading is ambiguous: {len(matches)} matches.")
    return matches[0]


def _find_all(body: str, needle: str) -> list[int]:
    """Non-overlapping match positions of ``needle`` in ``body``."""
    positions: list[int] = []
    start = 0
    step = max(len(needle), 1)
    while True:
        i = body.find(needle, start)
        if i == -1:
            break
        positions.append(i)
        start = i + step
    return positions


def _format_ambiguous(body: str, needle: str, positions: list[int]) -> str:
    """Render a diagnostic message listing each match's line, column and surrounding context."""
    lines = [f"Ambiguous: {len(positions)} matches:"]
    for pos in positions:
        before = body[:pos]
        line = before.count("\n") + 1
        last_nl = before.rfind("\n")
        col_start = last_nl + 1 if last_nl != -1 else 0
        column = len(body[col_start:pos]) + 1
        ctx_start = max(0, pos - 20)
        ctx_end = min(len(body), pos + len(needle) + 20)
        context = body[ctx_start:ctx_end].replace("\n", "\\n")
        lines.append(f"  line {line}, col {column}: ...{context}...")
    return "\n".join(lines)


def _ensure_nl(s: str) -> str:
    """Return ``s`` guaranteed to end with a newline."""
    return s if s.endswith("\n") else s + "\n"


def _locate_unique(body: str, needle: str) -> int:
    """Return the start index of the single occurrence of ``needle`` in ``body``.

    Raises ``AnchorNotFoundError`` on no match, ``AnchorAmbiguousError`` on 2+ matches.
    """
    positions = _find_all(body, needle)
    if not positions:
        raise AnchorNotFoundError("Text not found.")
    if len(positions) > 1:
        raise AnchorAmbiguousError(_format_ambiguous(body, needle, positions))
    return positions[0]


def _replace_text_all(body: str, old: str, new: str) -> tuple[str, int]:
    """Replace every non-overlapping occurrence of ``old`` with ``new``. Raises
    ``AnchorNotFoundError`` on zero occurrences (mirrors ``replace_text``'s contract).
    ``old`` is always non-empty here — callers (``apply_edit``) already require it.
    """
    positions = _find_all(body, old)
    if not positions:
        raise AnchorNotFoundError("Text not found.")
    return body.replace(old, new), len(positions)


def _splice_block(prefix: str, inserted: str, remainder: str) -> str:
    """Assemble ``prefix`` + ``inserted`` + ``remainder`` with newline normalization.

    Guarantees a newline after ``prefix`` and after ``inserted`` (applied to the running
    result, so an empty ``inserted`` adds no spurious blank line), and a blank-line
    separator before a non-empty ``remainder``.
    """
    result = _ensure_nl(prefix)
    result += inserted
    result = _ensure_nl(result)
    if remainder:
        result += "\n"
    result += remainder
    return result


def append_content(body: str, text: str, heading: str | None) -> str:
    """Append ``text`` at end of body, or at the end of ``heading``'s section."""
    if heading is None:
        return _splice_block(body, text, "")

    section = find_section_by_heading(parse_sections(body), heading)
    section_body = body[section.body_start : section.body_end]
    content_end = section.body_start + len(section_body.rstrip())
    return _splice_block(body[:content_end], text, body[section.body_end :])


def prepend_content(body: str, text: str, heading: str | None) -> str:
    """Prepend ``text`` at the start of body, or right after ``heading``'s line."""
    if heading is None:
        result = _ensure_nl(text)
        body_trimmed = body.lstrip("\n")
        if body_trimmed:
            result = _ensure_nl(result + body_trimmed)
        return result

    section = find_section_by_heading(parse_sections(body), heading)
    insert_pos = section.heading_end
    result = _ensure_nl(body[:insert_pos])
    result = _ensure_nl(result + text)
    result += body[insert_pos:]
    return result


def replace_section(body: str, heading: str, text: str) -> str:
    """Replace a section's body, preserving the heading line and following sections.

    If ``text`` opens with the same heading (a common mistake), it is stripped to avoid
    duplicating it.
    """
    section = find_section_by_heading(parse_sections(body), heading)

    body_only = text
    nl = text.find("\n")
    if nl != -1:
        first_line = text[:nl].lstrip()
        if first_line.startswith("#") and (
            first_line.lstrip("#").strip() == heading.lstrip("#").strip()
        ):
            body_only = text[nl + 1 :]

    return _splice_block(body[: section.heading_end], body_only, body[section.body_end :])


def replace_text(body: str, old: str, new: str) -> str:
    """Replace an exact, unique occurrence of ``old`` with ``new``. Errors on 0 or 2+ matches."""
    pos = _locate_unique(body, old)
    return body[:pos] + new + body[pos + len(old) :]


def insert_after(body: str, anchor: str, text: str) -> str:
    """Insert ``text`` immediately after a unique ``anchor``. Errors on 0 or 2+ matches."""
    pos = _locate_unique(body, anchor) + len(anchor)
    result = body[:pos]
    if not result.endswith("\n") and not text.startswith("\n"):
        result += "\n"
    result += text
    if not result.endswith("\n") and not body[pos:].startswith("\n"):
        result += "\n"
    result += body[pos:]
    return result


def _reject(mode: str, accepts: str, **unused: str | None) -> None:
    """Refuse a parameter that belongs to another mode, naming what this mode takes.

    Silently ignoring it would let a caller believe an edit landed the way they meant.
    """
    for name, value in unused.items():
        if value:
            raise ValueError(f"Mode '{mode}' does not take {name}; it takes {accepts}.")


def _require(mode: str, name: str, value: str | None) -> str:
    """Return a required, non-empty parameter — narrowing it to ``str`` for the caller."""
    if not value:
        raise ValueError(f"Mode '{mode}' requires {name}.")
    return value


def apply_edit(
    body: str,
    mode: str,
    *,
    content: str | None = None,
    old_str: str | None = None,
    new_str: str | None = None,
    target_heading: str | None = None,
    replace_all: bool = False,
) -> EditResult:
    """Dispatch to the transform for ``mode``, validating its parameter set.

    ``body`` is the current note body (no frontmatter). The whole-body modes
    (overwrite/append/prepend/replace_section) take ``content``; the text modes
    (replace_text/insert_after/delete_text) take the ``old_str`` anchor plus, except for
    delete_text, its ``new_str`` replacement. Every mode owns exactly one of those sets,
    and a parameter from the other set is a validation error rather than a silently
    dropped argument.

    ``overwrite`` with ``content=None`` leaves the body untouched — that is the
    metadata-only edit (title/tags/folder) coming through this same path, which is why it
    is handled here rather than short-circuited by the caller.

    ``replace_all`` only applies to replace_text/delete_text; there is no well-defined
    "all occurrences" for e.g. a section replace.

    Raises ``ValueError`` (or a subclass) on an invalid parameter set or a failed
    anchor/heading lookup.
    """
    if replace_all and mode not in ("replace_text", "delete_text"):
        raise ValueError(
            f"replace_all only applies to 'replace_text' and 'delete_text', not '{mode}'."
        )

    match mode:
        case "overwrite":
            _reject(
                mode, "content", old_str=old_str, new_str=new_str, target_heading=target_heading
            )
            return EditResult(body=body if content is None else content)

        case "append" | "prepend":
            _reject(mode, "content", old_str=old_str, new_str=new_str)
            splice: Callable[[str, str, str | None], str] = (
                append_content if mode == "append" else prepend_content
            )
            return EditResult(body=splice(body, _require(mode, "content", content), target_heading))

        case "replace_section":
            _reject(mode, "content", old_str=old_str, new_str=new_str)
            return EditResult(
                body=replace_section(
                    body,
                    _require(mode, "target_heading", target_heading),
                    _require(mode, "content", content),
                )
            )

        case "replace_text":
            _reject(mode, "old_str and new_str", content=content, target_heading=target_heading)
            anchor = _require(mode, "old_str", old_str)
            replacement = _require(mode, "new_str", new_str)
            if replace_all:
                new_body, count = _replace_text_all(body, anchor, replacement)
                return EditResult(body=new_body, replaced=count)
            return EditResult(body=replace_text(body, anchor, replacement))

        case "insert_after":
            _reject(mode, "old_str and new_str", content=content, target_heading=target_heading)
            return EditResult(
                body=insert_after(
                    body,
                    _require(mode, "old_str", old_str),
                    _require(mode, "new_str", new_str),
                )
            )

        case "delete_text":
            _reject(
                mode, "old_str", content=content, new_str=new_str, target_heading=target_heading
            )
            anchor = _require(mode, "old_str", old_str)
            if replace_all:
                new_body, count = _replace_text_all(body, anchor, "")
                return EditResult(body=new_body, replaced=count)
            return EditResult(body=replace_text(body, anchor, ""))

        case _:
            raise ValueError(f"Unknown edit mode: '{mode}'.")
