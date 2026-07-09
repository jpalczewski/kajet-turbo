"""Document outline: headings with edit-ready targets and section sizes, no content.

Built on ``note_edit.parse_sections`` (the same CommonMark-based parser ``edit_note``'s
surgical modes use) rather than the chunking pipeline's breadcrumb parser — the outline's
whole purpose is producing ``target_heading`` values that work with ``edit_note``, so it
must share that exact heading-matching semantics (top-level only, code-fence-safe).
"""

from dataclasses import dataclass

from kajet_turbo.markdown.note_edit import parse_sections


@dataclass(frozen=True, slots=True)
class OutlineSection:
    level: int
    heading: str
    target_heading: str
    line_start: int
    line_end: int
    char_start: int
    char_end: int
    section_chars: int
    section_lines: int
    ambiguous: bool


def _line_number(text: str, char_index: int) -> int:
    """1-based line number containing ``char_index`` (clamped to len(text))."""
    return text.count("\n", 0, min(char_index, len(text))) + 1


def build_outline(markdown: str) -> tuple[list[OutlineSection], int, int]:
    """Document outline: headings with ``target_heading`` (paste directly into
    ``edit_note(mode='replace_section', target_heading=...)``), line/char spans, and
    section sizes — without section content. ``ambiguous=True`` marks a heading whose
    text repeats elsewhere in the document, where ``edit_note``'s target_heading lookup
    would raise ``HeadingAmbiguousError``. Returns ``(sections, preamble_chars,
    preamble_lines)`` — preamble is any body text before the first heading.
    """
    sections = parse_sections(markdown)
    heading_texts = [s.heading_text.strip() for s in sections]
    duplicate_texts = {t for t in heading_texts if heading_texts.count(t) > 1}

    preamble_chars = sections[0].heading_start if sections else len(markdown)
    # Trailing blank lines right before the first heading are separator, not preamble
    # content — rstrip them before counting so ``preamble_lines`` reports content lines,
    # matching what test_build_outline_preamble_before_first_heading expects (2, not 3).
    preamble_text = markdown[:preamble_chars].rstrip("\n")
    preamble_lines = preamble_text.count("\n") + 1 if preamble_text else 0

    outline: list[OutlineSection] = []
    for section in sections:
        heading_text = section.heading_text.strip()
        line_start = section.heading_line + 1
        # section.body_end is an *exclusive* boundary (the next heading's start, or
        # doc_len) — it always lands at the start of a line, one line past this
        # section's real content. Ask for the line containing the last char actually
        # in the section (body_end - 1) instead of the line body_end starts on, or a
        # trailing-blank-line-free section (e.g. two headings on consecutive lines)
        # reports a line_end one past the next section's line_start.
        line_end = _line_number(markdown, max(section.body_end - 1, section.heading_start))
        outline.append(
            OutlineSection(
                level=section.level,
                heading=heading_text,
                target_heading=f"{'#' * section.level} {heading_text}",
                line_start=line_start,
                line_end=line_end,
                char_start=section.heading_start,
                char_end=section.body_end,
                section_chars=section.body_end - section.heading_start,
                section_lines=line_end - line_start + 1,
                ambiguous=heading_text in duplicate_texts,
            )
        )
    return outline, preamble_chars, preamble_lines
