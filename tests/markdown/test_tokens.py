import pytest
from markdown_it import MarkdownIt

from kajet_turbo.markdown._tokens import (
    Heading,
    extract_meta,
    iter_headings,
    line_offsets,
    walk_tokens,
)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("", [0]),
        ("abc", [0, 3]),
        ("a\nb\nc", [0, 2, 4, 5]),
        ("a\nb\nc\n", [0, 2, 4, 6]),  # trailing LF: no duplicate tail
        ("a\r\nb", [0, 3, 4]),  # CRLF is one break
        ("a\rb", [0, 2, 3]),  # bare CR IS a break (markdown-it normalizes it)
        ("a\x0bb", [0, 3]),  # vertical tab is NOT a break (len("a\x0bb") == 3)
        ("a\u2028b", [0, 3]),  # U+2028 is NOT a break (len == 3)
    ],
)
def test_line_offsets_matches_markdown_it_semantics(text, expected):
    assert line_offsets(text) == expected


def test_line_offsets_aligns_with_token_map():
    from markdown_it import MarkdownIt

    text = "# h\r\n\r\nbody"
    md = MarkdownIt("commonmark")
    offsets = line_offsets(text)
    for tok in md.parse(text):
        if tok.map is not None:
            start_line, end_line = tok.map
            # offsets must be indexable at both map endpoints
            assert 0 <= start_line < len(offsets)
            assert 0 <= end_line < len(offsets)


def test_walk_tokens_descends_into_inline_children():
    md = MarkdownIt("commonmark")
    tokens = md.parse("# heading\n\nparagraph *em*")
    types = [t.type for t in walk_tokens(tokens)]
    # inline children (text/em_open) only appear if we descended into the inline token
    assert "inline" in types
    assert "em_open" in types


def test_extract_meta_collects_meta_of_matching_type():
    md = MarkdownIt("commonmark")

    def rule(state, silent):
        if state.src[state.pos] != "@":
            return False
        if not silent:
            tok = state.push("at_token", "", 0)
            tok.meta = {"name": state.src[state.pos + 1 : state.pos + 4]}
        state.pos += 4
        return True

    md.inline.ruler.before("link", "at_token", rule)
    metas = list(extract_meta(md, "hello @bob world", "at_token"))
    assert metas == [{"name": "bob"}]


def test_extract_meta_ignores_code_spans():
    md = MarkdownIt("commonmark")

    def rule(state, silent):
        if state.src[state.pos] != "@":
            return False
        if not silent:
            tok = state.push("at_token", "", 0)
            tok.meta = {"name": "x"}
        state.pos += 1
        return True

    md.inline.ruler.before("link", "at_token", rule)
    assert list(extract_meta(md, "`@x`", "at_token")) == []


def test_iter_headings_basic_levels_text_and_lines():
    md = MarkdownIt("commonmark")
    tokens = md.parse("# Title\n\nintro\n\n## Sub\n\nbody")
    headings = list(iter_headings(tokens))
    assert headings == [
        Heading(level=1, text="Title", open_line=0, body_line=1),
        Heading(level=2, text="Sub", open_line=4, body_line=5),
    ]


def test_iter_headings_top_level_only_skips_nested():
    md = MarkdownIt("commonmark")
    # heading inside a blockquote has token.level != 0
    tokens = md.parse("# Top\n\n> # Nested\n")
    all_h = [h.text for h in iter_headings(tokens)]
    top_h = [h.text for h in iter_headings(tokens, top_level_only=True)]
    assert "Nested" in all_h
    assert top_h == ["Top"]
