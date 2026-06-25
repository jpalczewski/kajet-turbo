import pytest

from kajet_turbo.markdown import DEFAULT_HARD_MAX, Chunk, chunk_markdown, embedded_text
from kajet_turbo.markdown._tokens import Heading, iter_headings
from kajet_turbo.markdown.chunking import (
    _MD,
    _blocks,
    _build_sections,
    _common_prefix,
    _merge_small,
    _Section,
    _split_body,
    _split_oversized_block,
)


def test_embedded_text_prepends_breadcrumb():
    c = Chunk(
        ordinal=0, header_path=["# Title", "## Sec"], content="body text", char_start=0, char_end=9
    )
    assert embedded_text(c) == "# Title\n## Sec\n\nbody text"


def test_embedded_text_no_header_path_is_just_content():
    c = Chunk(ordinal=0, header_path=[], content="lone body", char_start=0, char_end=9)
    assert embedded_text(c) == "lone body"


def test_extract_headings_levels_text_and_lines():
    text = "# Title\n\nintro\n\n## Section A\n\nbody\n\n### Deep\n"
    hs = list(iter_headings(_MD.parse(text)))
    assert hs == [
        Heading(level=1, text="Title", open_line=0, body_line=1),
        Heading(level=2, text="Section A", open_line=4, body_line=5),
        Heading(level=3, text="Deep", open_line=8, body_line=9),
    ]


def test_extract_headings_ignores_hash_inside_code_fence():
    text = "# Real\n\n```\n# not a heading\n```\n"
    hs = list(iter_headings(_MD.parse(text)))
    assert [h.text for h in hs] == ["Real"]


def test_extract_headings_empty_when_none():
    assert list(iter_headings(_MD.parse("just a paragraph\nsecond line\n"))) == []


def test_build_sections_header_path_stack():
    text = "# Title\n\nintro\n\n## A\n\nbody a\n\n### A1\n\ndeep\n\n## B\n\nbody b\n"
    secs = _build_sections(text, title=None)
    paths = [s.header_path for s in secs]
    assert paths == [
        ["# Title"],  # preamble belongs under the H1 title
        ["# Title", "## A"],
        ["# Title", "## A", "### A1"],
        ["# Title", "## B"],
    ]
    assert secs[1].content.strip() == "body a"
    # char offsets map back to the original text
    assert text[secs[1].char_start : secs[1].char_end] == secs[1].content


def test_build_sections_injects_passed_title_when_no_h1():
    text = "## Only H2\n\nbody\n"
    secs = _build_sections(text, title="Frontmatter Title")
    assert secs[0].header_path == ["# Frontmatter Title", "## Only H2"]


def test_build_sections_preamble_only_no_headings():
    secs = _build_sections("just text\nmore text\n", title="T")
    assert len(secs) == 1
    assert secs[0].header_path == ["# T"]
    assert secs[0].content.strip() == "just text\nmore text"


def test_common_prefix():
    assert _common_prefix(["# T", "## A"], ["# T", "## B"]) == ["# T"]
    assert _common_prefix(["# T"], ["# T", "## A"]) == ["# T"]
    assert _common_prefix(["# X"], ["# Y"]) == []


def test_merge_small_combines_below_min_under_common_path():
    secs = [
        _Section(["# T", "## A"], "short a", 0, 7),
        _Section(["# T", "## B"], "short b", 7, 14),
    ]
    merged = _merge_small(secs, target=1400, min_size=200)
    assert len(merged) == 1
    assert merged[0].header_path == ["# T"]
    assert merged[0].content == "short a\n\nshort b"
    assert (merged[0].char_start, merged[0].char_end) == (0, 14)


def test_merge_small_keeps_large_sections_separate():
    big = "x" * 300
    secs = [_Section(["# T", "## A"], big, 0, 300), _Section(["# T", "## B"], big, 300, 600)]
    merged = _merge_small(secs, target=1400, min_size=200)
    assert len(merged) == 2  # neither is below min_size


def test_split_body_under_limit_is_one_piece():
    assert _split_body("small body", hard_max=2000) == ["small body"]


def test_split_body_splits_at_blank_line_boundaries():
    body = ("a" * 1500) + "\n\n" + ("b" * 1500)
    pieces = _split_body(body, hard_max=2000)
    assert len(pieces) == 2
    assert all(len(p) <= 2000 for p in pieces)
    assert pieces[0].strip() == "a" * 1500


def test_split_body_keeps_fence_atomic():
    fence = "```py\n" + "x = 1\n" * 3 + "```"
    # paragraph alone fits hard_max, but paragraph + fence exceeds it, forcing a split;
    # the fence must then land in its own piece rather than be broken apart.
    body = ("p " * 1000).strip() + "\n\n" + fence
    pieces = _split_body(body, hard_max=2000)
    # the fence stays in a single piece, never broken across pieces
    assert any(p.strip() == fence for p in pieces)


def test_split_body_oversized_paragraph_splits_on_sentences():
    body = ("Zdanie pierwsze. " * 200).strip()  # > hard_max, no blank lines
    pieces = _split_body(body, hard_max=2000)
    assert len(pieces) > 1
    assert all(len(p) <= 2000 for p in pieces)


def test_chunk_markdown_ordinals_and_paths():
    # Explicit ``# Title`` H1: the "intro" preamble belongs to the H1 section (no separate
    # preamble chunk), so this yields 3 chunks: [# Title], [# Title, ## A], [# Title, ## B].
    text = "# Title\n\nintro\n\n## A\n\nbody a\n\n## B\n\nbody b\n"
    chunks = chunk_markdown(text, title="Title", min_size=0)  # min_size=0 disables merge
    assert [c.ordinal for c in chunks] == [0, 1, 2]
    assert chunks[0].header_path == ["# Title"]
    assert chunks[1].header_path == ["# Title", "## A"]


def test_chunk_markdown_oversized_section_yields_multiple_chunks():
    big = ("para " * 500).strip()  # one oversized paragraph
    text = f"# T\n\n## Big\n\n{big}\n"
    chunks = chunk_markdown(text, title="T")
    big_chunks = [c for c in chunks if c.header_path == ["# T", "## Big"]]
    assert len(big_chunks) > 1
    assert all(len(c.content) <= DEFAULT_HARD_MAX for c in chunks)


def test_chunk_markdown_empty_text_is_empty():
    assert chunk_markdown("", title="T") == []
    assert chunk_markdown("   \n\n  \n", title="T") == []


def test_chunk_markdown_unicode_diacritics_preserved():
    text = "# Zażółć\n\ngęślą jaźń źrebię\n"
    chunks = chunk_markdown(text, title="Zażółć")
    assert chunks[0].content.strip() == "gęślą jaźń źrebię"
    assert chunks[0].header_path == ["# Zażółć"]


# --- fix #1: tilde (~~~) code fences are fences too ---


def test_blocks_tilde_fence_with_blank_line_stays_atomic():
    body = "~~~py\nx = 1\n\ny = 2\n~~~"
    assert _blocks(body) == [body]


def test_split_body_tilde_fence_with_blank_line_stays_atomic():
    fence = "~~~py\nx = 1\n\ny = 2\n~~~"
    body = ("p " * 1000).strip() + "\n\n" + fence
    pieces = _split_body(body, hard_max=2000)
    # the tilde fence (containing a blank line) must not be split mid-fence
    assert any(p.strip() == fence for p in pieces)


def test_split_oversized_tilde_fence_is_line_split():
    fence = "~~~\n" + ("code line here\n" * 400) + "~~~"
    assert len(fence) > DEFAULT_HARD_MAX
    pieces = _split_oversized_block(fence, DEFAULT_HARD_MAX)
    assert len(pieces) > 1
    assert all(len(p) <= DEFAULT_HARD_MAX for p in pieces)
    # line-split, not sentence-split: first piece keeps the opener, last the closer
    assert pieces[0].startswith("~~~")
    assert pieces[-1].rstrip().endswith("~~~")


def test_tilde_does_not_close_backtick_fence_and_vice_versa():
    # a ``` fence whose body contains a ~~~ line must stay a single block
    body = "```\n~~~ not a closer\nstill code\n```"
    assert _blocks(body) == [body]


# --- fix #2: char_start/char_end are section provenance, not content slices ---


def test_char_span_sub_split_pieces_share_section_span():
    big = ("para " * 500).strip()  # one oversized paragraph -> sub-split
    text = f"# T\n\n## Big\n\n{big}\n"
    chunks = chunk_markdown(text, title="T")
    big_chunks = [c for c in chunks if c.header_path == ["# T", "## Big"]]
    assert len(big_chunks) > 1
    spans = {(c.char_start, c.char_end) for c in big_chunks}
    # all sub-split pieces share the one originating section span (provenance)
    assert len(spans) == 1
    start, end = next(iter(spans))
    # content is NOT necessarily reconstructable from the span slice
    assert text[start:end] != big_chunks[0].content


def test_char_span_merged_section_covers_first_start_to_last_end():
    secs = [
        _Section(["# T", "## A"], "short a", 10, 20),
        _Section(["# T", "## B"], "short b", 20, 33),
    ]
    merged = _merge_small(secs, target=1400, min_size=200)
    assert len(merged) == 1
    # span covers first.start .. last.end; content does not equal the slice
    assert (merged[0].char_start, merged[0].char_end) == (10, 33)


# --- fix #4: validation of size parameters ---


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_size": -1},
        {"target": 3000, "hard_max": 2000},  # target > hard_max
        {"min_size": 500, "target": 300},  # min_size > target
    ],
)
def test_chunk_markdown_invalid_sizes_raise_value_error(kwargs):
    with pytest.raises(ValueError):
        chunk_markdown("# T\n\nbody\n", title="T", **kwargs)


# --- fix #5: missing-branch coverage ---


def test_split_body_flushes_buffer_before_oversized_block():
    # a small block followed by an oversized block: the small one must be flushed first
    small = "small para"
    big = ("Zdanie pierwsze. " * 200).strip()  # > hard_max
    body = small + "\n\n" + big
    pieces = _split_body(body, hard_max=2000)
    assert pieces[0] == small
    assert all(len(p) <= 2000 for p in pieces)


def test_explicit_h1_plus_matching_title_not_double_injected():
    text = "# Title\n\nintro\n"
    secs = _build_sections(text, title="Title")
    # the explicit H1 already heads the path; the passed title must not be re-prepended
    assert secs[0].header_path == ["# Title"]


def test_merge_boundary_combined_equals_target_still_merges():
    # combined == target exactly must still merge (<=, not <)
    a = "a" * 100
    b = "b" * 100  # combined content = 100 + 100 = 200 == target
    secs = [_Section(["# T"], a, 0, 100), _Section(["# T"], b, 100, 200)]
    merged = _merge_small(secs, target=200, min_size=200)
    assert len(merged) == 1
