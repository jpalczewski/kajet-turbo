from kajet_turbo.markdown.outline import build_outline


def test_build_outline_basic_sections():
    md = "# Title\n\nintro\n\n## First\n\nbody one\n\n## Second\n\nbody two\n"
    sections, preamble_chars, preamble_lines = build_outline(md)
    assert [s.heading for s in sections] == ["Title", "First", "Second"]
    assert [s.level for s in sections] == [1, 2, 2]
    assert sections[0].target_heading == "# Title"
    assert sections[1].target_heading == "## First"
    assert preamble_chars == 0
    assert preamble_lines == 0


def test_build_outline_line_spans_are_one_based_and_contiguous():
    md = "# A\nbody a\n# B\nbody b\n"
    sections, _, _ = build_outline(md)
    assert sections[0].line_start == 1
    assert sections[1].line_start == 3
    assert sections[0].line_end == sections[1].line_start - 1


def test_build_outline_section_sizes_match_content():
    md = "# A\n\n1234567890\n"
    sections, _, _ = build_outline(md)
    assert sections[0].section_chars > 0
    assert sections[0].section_lines >= 1


def test_build_outline_preamble_before_first_heading():
    md = "some intro text\nmore intro\n\n# First heading\n\nbody\n"
    _sections, preamble_chars, preamble_lines = build_outline(md)
    assert preamble_chars == md.index("# First heading")
    assert preamble_lines == 2


def test_build_outline_no_headings_is_all_preamble():
    md = "just a paragraph, no headings\n"
    sections, preamble_chars, _preamble_lines = build_outline(md)
    assert sections == []
    assert preamble_chars == len(md)


def test_build_outline_duplicate_heading_marked_ambiguous():
    md = "# A\n\none\n\n# A\n\ntwo\n"
    sections, _, _ = build_outline(md)
    assert sections[0].ambiguous is True
    assert sections[1].ambiguous is True


def test_build_outline_unique_heading_not_ambiguous():
    md = "# A\n\none\n\n# B\n\ntwo\n"
    sections, _, _ = build_outline(md)
    assert sections[0].ambiguous is False
    assert sections[1].ambiguous is False


def test_build_outline_ignores_headings_in_fenced_code():
    md = "# Real\n\n```\n# not a heading\n```\n\nbody\n"
    sections, _, _ = build_outline(md)
    assert [s.heading for s in sections] == ["Real"]


def test_build_outline_target_heading_resolves_via_find_section_by_heading():
    from kajet_turbo.markdown.note_edit import find_section_by_heading, parse_sections

    md = "# Title\n\n## Tasks\n\n- one\n\n## Notes\n\ntext\n"
    sections, _, _ = build_outline(md)
    tasks_outline = next(s for s in sections if s.heading == "Tasks")
    # The outline's target_heading must be exactly what edit_note's target_heading expects.
    found = find_section_by_heading(parse_sections(md), tasks_outline.target_heading)
    assert found.heading_text == "Tasks"
