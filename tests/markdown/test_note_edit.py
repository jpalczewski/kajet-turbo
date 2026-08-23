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
from kajet_turbo.markdown.note_edit import (
    _ACCEPTS,
    _ensure_nl,
    _locate_unique,
    _splice_block,
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
    assert "line 1, col 1" in msg


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
    assert apply_edit("old", "overwrite", content="new").body == "new"


def test_apply_edit_overwrite_without_content_keeps_body():
    """The metadata-only edit path: edit_note(title=...) leaves the body untouched."""
    assert apply_edit("old", "overwrite").body == "old"


@pytest.mark.parametrize(
    ("mode", "kwargs", "missing"),
    [
        ("replace_section", {"content": "x"}, "target_heading"),
        ("replace_text", {"new_str": "x"}, "old_str"),
        ("replace_text", {"old_str": "x"}, "new_str"),
        ("insert_after", {"old_str": "x"}, "new_str"),
        ("delete_text", {}, "old_str"),
        ("append", {}, "content"),
    ],
)
def test_apply_edit_requires_its_own_parameters(mode, kwargs, missing):
    with pytest.raises(ValueError, match=f"requires {missing}"):
        apply_edit("body", mode, **kwargs)


def test_apply_edit_rejects_an_unknown_mode():
    with pytest.raises(ValueError, match="Unknown edit mode"):
        apply_edit("body", "bogus", content="x")


@pytest.mark.parametrize(
    ("mode", "foreign"),
    [
        (mode, param)
        for mode, accepted in _ACCEPTS.items()
        for param in ("content", "old_str", "new_str", "target_heading")
        if param not in accepted
    ],
)
def test_apply_edit_rejects_every_parameter_a_mode_does_not_own(mode, foreign):
    """Generated from _ACCEPTS, so a new mode cannot quietly skip its rejection rule."""
    passed = dict.fromkeys(("content", "old_str", "new_str", "target_heading"))
    passed[foreign] = "x"
    with pytest.raises(ValueError) as exc:
        apply_edit(
            "body",
            mode,
            content=passed["content"],
            old_str=passed["old_str"],
            new_str=passed["new_str"],
            target_heading=passed["target_heading"],
        )
    assert f"does not take {foreign}" in str(exc.value)


def test_apply_edit_rejects_a_foreign_parameter_even_when_empty():
    """Presence, not truthiness — content="" is still the caller using the wrong parameter."""
    with pytest.raises(ValueError, match="does not take content"):
        apply_edit("body", "replace_text", old_str="a", new_str="b", content="")


def test_apply_edit_routes_to_modes():
    assert apply_edit("a\n", "append", content="b").body == "a\nb\n"
    assert apply_edit("foo bar", "replace_text", old_str="foo", new_str="qux").body == "qux bar"
    assert apply_edit("a", "insert_after", old_str="a", new_str="b").body == "a\nb\n"
    assert apply_edit("keep [drop] keep", "delete_text", old_str="[drop] ").body == "keep keep"


def test_apply_edit_replace_text_replace_all_returns_count():
    result = apply_edit(
        "foo bar foo baz foo", "replace_text", old_str="foo", new_str="qux", replace_all=True
    )
    assert result.body == "qux bar qux baz qux"
    assert result.replaced == 3


def test_apply_edit_delete_text_replace_all_returns_count():
    result = apply_edit("x foo y foo z", "delete_text", old_str="foo ", replace_all=True)
    assert result.body == "x y z"
    assert result.replaced == 2


def test_apply_edit_replace_all_no_match_raises():
    with pytest.raises(AnchorNotFoundError):
        apply_edit("no match here", "replace_text", old_str="zzz", new_str="x", replace_all=True)


def test_apply_edit_replace_all_rejects_non_text_mode():
    with pytest.raises(ValueError, match="replace_all"):
        apply_edit("body", "append", content="x", replace_all=True)


def test_apply_edit_without_replace_all_keeps_uniqueness_requirement():
    with pytest.raises(AnchorAmbiguousError):
        apply_edit("foo bar foo", "replace_text", old_str="foo", new_str="qux")


def test_apply_edit_non_replace_all_modes_have_replaced_none():
    assert apply_edit("old", "overwrite", content="new").replaced is None
    assert apply_edit("a\n", "append", content="b").replaced is None


def test_polish_content_append_to_section():
    content = "# Główne tematy\n\nTreść.\n\n## Współpraca\n\nSzczegóły.\n"
    result = append_content(content, "Nowa treść.", "## Współpraca")
    assert "Szczegóły." in result and "Nowa treść." in result


# -- private helpers --


def test_ensure_nl():
    assert _ensure_nl("a") == "a\n"
    assert _ensure_nl("a\n") == "a\n"
    assert _ensure_nl("") == "\n"


def test_locate_unique_ok_and_errors():
    assert _locate_unique("hello world", "world") == 6
    with pytest.raises(AnchorNotFoundError):
        _locate_unique("hello", "zzz")
    with pytest.raises(AnchorAmbiguousError):
        _locate_unique("a a a", "a")


def test_splice_block_adds_surrounding_newlines_and_separator():
    assert _splice_block("head", "body", "rest") == "head\nbody\n\nrest"


def test_splice_block_empty_inserted_does_not_add_blank_line():
    # prefix already ends with \n; empty insert must NOT introduce an extra blank line
    assert _splice_block("head\n", "", "rest") == "head\n\nrest"


def test_splice_block_empty_remainder_has_no_trailing_separator():
    assert _splice_block("head", "body", "") == "head\nbody\n"
