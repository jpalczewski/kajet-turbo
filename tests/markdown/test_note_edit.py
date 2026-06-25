import pytest

from kajet_turbo.markdown import (
    AnchorAmbiguousError,
    AnchorNotFoundError,
    HeadingAmbiguousError,
    HeadingNotFoundError,
    append_content,
    apply_edit,
    find_section_by_heading,
    insert_after,
    parse_sections,
    prepend_content,
    replace_section,
    replace_text,
)

# -- parse_sections / find_section_by_heading --


def test_parse_empty_and_no_headings():
    assert parse_sections("") == []
    assert parse_sections("Just plain text without headings.") == []


def test_parse_single_heading_ranges():
    md = "# Title\n\nSome body text.\n"
    sections = parse_sections(md)
    assert len(sections) == 1
    assert sections[0].level == 1
    assert sections[0].heading_text == "Title"
    assert md[sections[0].heading_start : sections[0].heading_end] == "# Title\n"
    assert md[sections[0].body_start : sections[0].body_end].strip() == "Some body text."


def test_parse_nested_headings_body_includes_subsections():
    md = "# H1\n\nH1 body\n\n## H2\n\nH2 body\n\n### H3\n\nH3 body\n"
    sections = parse_sections(md)
    assert [s.level for s in sections] == [1, 2, 3]
    h1_body = md[sections[0].body_start : sections[0].body_end]
    assert "H2 body" in h1_body and "H3 body" in h1_body
    h2_body = md[sections[1].body_start : sections[1].body_end]
    assert "H3 body" in h2_body
    h3_body = md[sections[2].body_start : sections[2].body_end]
    assert "H2 body" not in h3_body


def test_parse_ignores_headings_in_code_blocks():
    md = "# Real heading\n\nBody\n\n```markdown\n# Fake heading\n```\n\nMore body\n"
    sections = parse_sections(md)
    assert len(sections) == 1
    assert sections[0].heading_text == "Real heading"


def test_hashtag_without_space_is_not_heading():
    assert parse_sections("#notaheading\n\nbody\n") == []


def test_parse_detects_setext_headings():
    md = "Wstęp\n=====\n\nbody\n\nPodsekcja\n---------\n\nx\n"
    sections = parse_sections(md)
    assert [(s.level, s.heading_text) for s in sections] == [(1, "Wstęp"), (2, "Podsekcja")]
    # The heading range spans the text line plus the underline line.
    assert md[sections[0].heading_start : sections[0].heading_end] == "Wstęp\n=====\n"


def test_append_to_setext_section():
    md = "Zadania\n-------\n\n- Pierwsze\n\n## Inne\n\nx\n"
    result = append_content(md, "- Drugie", "Zadania")
    assert "- Pierwsze\n- Drugie" in result
    assert result.index("- Drugie") < result.index("## Inne")


def test_replace_setext_section_preserves_underline():
    md = "Notatki\n-------\n\nstare\n\n## Inne\n\ny\n"
    result = replace_section(md, "Notatki", "nowe")
    assert "Notatki\n-------" in result
    assert "nowe" in result and "stare" not in result
    assert "## Inne" in result


def test_heading_with_inline_markup_is_found():
    md = "## **Pogrubiony** nagłówek\n\nbody\n"
    sections = parse_sections(md)
    assert sections[0].heading_text == "**Pogrubiony** nagłówek"
    assert find_section_by_heading(sections, "## **Pogrubiony** nagłówek").level == 2


def test_nested_headings_in_blockquote_and_list_are_not_sections():
    md = "# Top\n\n> # W cytacie\n\n- ## W liście\n\n## Realny\n\nx\n"
    sections = parse_sections(md)
    assert [s.heading_text for s in sections] == ["Top", "Realny"]


def test_find_section_accepts_hash_prefix_and_reports_errors():
    sections = parse_sections("# Title\n\n## Tasks\n\nlist\n")
    assert find_section_by_heading(sections, "## Tasks").level == 2
    assert find_section_by_heading(sections, "Tasks").heading_text == "Tasks"
    with pytest.raises(HeadingNotFoundError):
        find_section_by_heading(sections, "Nieistnieje")
    dup = parse_sections("## Notes\n\nA\n\n## Notes\n\nB\n")
    with pytest.raises(HeadingAmbiguousError):
        find_section_by_heading(dup, "Notes")


# -- append --


def test_append_to_file():
    result = append_content("# Title\n\nExisting body.\n", "New line.", None)
    assert result.endswith("New line.\n")
    assert "Existing body." in result


def test_append_to_section_stays_before_next_heading():
    content = "# Title\n\nBody\n\n## Tasks\n\n- Task 1\n\n## Notes\n\nNote body\n"
    result = append_content(content, "- Task 2", "## Tasks")
    assert result.index("- Task 2") < result.index("## Notes")
    assert "- Task 1\n- Task 2" in result
    assert "- Task 2\n\n## Notes" in result


# -- prepend --


def test_prepend_no_heading_goes_first():
    result = prepend_content("# Title\n\nBody\n", "Prepended text.", None)
    assert result.index("Prepended text.") < result.index("# Title")


def test_prepend_with_heading_inserts_after_heading_line():
    content = "# Title\n\nBody\n\n## Tasks\n\nExisting tasks.\n"
    result = prepend_content(content, "New task.", "## Tasks")
    assert result.index("## Tasks") < result.index("New task.") < result.index("Existing tasks.")


# -- replace_section --


def test_replace_section_basic_keeps_following_sections():
    content = "# Title\n\n## Tasks\n\n- Old task\n\n## Notes\n\nNote body\n"
    result = replace_section(content, "## Tasks", "- New task 1\n- New task 2")
    assert "- New task 1" in result and "- New task 2" in result
    assert "- Old task" not in result
    assert "## Notes" in result and "Note body" in result
    assert "- New task 2\n\n## Notes" in result


def test_replace_section_includes_subsections():
    content = "## Parent\n\nParent body\n\n### Child\n\nChild body\n\n## Sibling\n\nSibling body\n"
    result = replace_section(content, "## Parent", "Replaced content.")
    assert "Replaced content." in result
    assert "Parent body" not in result and "Child body" not in result
    assert "## Sibling" in result and "Sibling body" in result


def test_replace_section_strips_duplicate_heading():
    content = "# Title\n\n## Tasks\n\n- Old task\n\n## Notes\n\nNote body\n"
    result = replace_section(content, "## Tasks", "## Tasks\n\n- New task 1\n- New task 2")
    assert result.count("## Tasks") == 1
    assert "- New task 1" in result and "- Old task" not in result


def test_replace_section_strips_heading_with_emoji_and_diacritics():
    content = "# 2026-02-09\n\n## 🌈 Główne Wątki\n\nStara treść\n\n## Inne\n\nInna treść\n"
    result = replace_section(content, "## 🌈 Główne Wątki", "## 🌈 Główne Wątki\n\nNowa treść")
    assert result.count("## 🌈 Główne Wątki") == 1
    assert "Nowa treść" in result and "Stara treść" not in result


def test_replace_section_heading_not_found():
    with pytest.raises(HeadingNotFoundError):
        replace_section("# Title\n\n## Tasks\n\nBody\n", "## Nieistnieje", "New")


# -- replace_text --


def test_replace_text_single():
    assert replace_text("Hello world, test.", "world", "earth") == "Hello earth, test."


def test_replace_text_multiline():
    assert replace_text("a\nLine two\nb\n", "Line two", "LINE TWO") == "a\nLINE TWO\nb\n"


def test_replace_text_not_found():
    with pytest.raises(AnchorNotFoundError):
        replace_text("Hello world.", "mars", "earth")


def test_replace_text_ambiguous_reports_positions():
    with pytest.raises(AnchorAmbiguousError) as exc:
        replace_text("foo bar foo baz foo", "foo", "qux")
    msg = str(exc.value)
    assert "3" in msg
    assert "linia 1, kol 1" in msg


def test_replace_text_empty_content_deletes():
    assert replace_text("keep [drop] keep", "[drop] ", "") == "keep keep"


# -- insert_after --


def test_insert_after_basic_bridges_newlines():
    content = "# Title\n\n- Item 1\n- Item 2\n\n## Notes\n"
    result = insert_after(content, "- Item 1", "- Item 1.5")
    assert "- Item 1\n- Item 1.5\n- Item 2" in result


def test_insert_after_multiline_anchor():
    result = insert_after("First line\nSecond line\nThird line\n", "First line\nSecond line", "X")
    assert "Second line\nX\nThird line" in result


def test_insert_after_not_found_and_ambiguous():
    with pytest.raises(AnchorNotFoundError):
        insert_after("Hello world.", "mars", "new")
    with pytest.raises(AnchorAmbiguousError):
        insert_after("foo bar foo", "foo", "new")


# -- apply_edit dispatch + validation --


def test_apply_edit_overwrite_returns_content():
    assert apply_edit("old", "overwrite", "new", None, None) == "new"


def test_apply_edit_validation_errors():
    with pytest.raises(ValueError, match="replace_section"):
        apply_edit("body", "replace_section", "x", None, None)
    with pytest.raises(ValueError, match="old_text"):
        apply_edit("body", "replace_text", "x", None, None)
    with pytest.raises(ValueError, match="overwrite"):
        apply_edit("body", "overwrite", "x", "## H", None)
    with pytest.raises(ValueError, match="pusty"):
        apply_edit("body", "append", "", None, None)
    with pytest.raises(ValueError, match="Nieznany"):
        apply_edit("body", "bogus", "x", None, None)


def test_apply_edit_routes_to_modes():
    assert apply_edit("a\n", "append", "b", None, None) == "a\nb\n"
    assert apply_edit("foo bar", "replace_text", "qux", None, "foo") == "qux bar"
    assert apply_edit("a", "insert_after", "b", None, "a") == "a\nb\n"


def test_polish_content_append_to_section():
    content = "# Główne tematy\n\nTreść.\n\n## Współpraca\n\nSzczegóły.\n"
    result = append_content(content, "Nowa treść.", "## Współpraca")
    assert "Szczegóły." in result and "Nowa treść." in result
