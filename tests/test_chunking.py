from kajet_turbo.chunking import Chunk, embedded_text


def test_embedded_text_prepends_breadcrumb():
    c = Chunk(ordinal=0, header_path=["# Title", "## Sec"], content="body text", char_start=0, char_end=9)
    assert embedded_text(c) == "# Title\n## Sec\n\nbody text"


def test_embedded_text_no_header_path_is_just_content():
    c = Chunk(ordinal=0, header_path=[], content="lone body", char_start=0, char_end=9)
    assert embedded_text(c) == "lone body"
