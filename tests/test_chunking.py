from kajet_turbo.chunking import (
    Chunk,
    _extract_headings,
    _Heading,
    embedded_text,
)


def test_embedded_text_prepends_breadcrumb():
    c = Chunk(ordinal=0, header_path=["# Title", "## Sec"], content="body text", char_start=0, char_end=9)
    assert embedded_text(c) == "# Title\n## Sec\n\nbody text"


def test_embedded_text_no_header_path_is_just_content():
    c = Chunk(ordinal=0, header_path=[], content="lone body", char_start=0, char_end=9)
    assert embedded_text(c) == "lone body"


def test_extract_headings_levels_text_and_lines():
    text = "# Title\n\nintro\n\n## Section A\n\nbody\n\n### Deep\n"
    hs = _extract_headings(text)
    assert hs == [
        _Heading(open_line=0, body_line=1, level=1, text="Title"),
        _Heading(open_line=4, body_line=5, level=2, text="Section A"),
        _Heading(open_line=8, body_line=9, level=3, text="Deep"),
    ]


def test_extract_headings_ignores_hash_inside_code_fence():
    text = "# Real\n\n```\n# not a heading\n```\n"
    hs = _extract_headings(text)
    assert [h.text for h in hs] == ["Real"]


def test_extract_headings_empty_when_none():
    assert _extract_headings("just a paragraph\nsecond line\n") == []
