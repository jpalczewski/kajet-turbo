from kajet_turbo.tags import ancestors, extract_inline_tags, normalize, segments


def test_normalize_strips_hash_and_lowercases():
    assert normalize("#Work/Projects") == "work/projects"


def test_normalize_collapses_and_trims_slashes():
    assert normalize("/work//projects/") == "work/projects"


def test_normalize_rejects_empty_and_invalid():
    assert normalize("") is None
    assert normalize("#") is None
    assert normalize("work project") is None  # space not allowed
    assert normalize("a/b!c") is None


def test_normalize_allows_unicode_and_hyphen_underscore():
    assert normalize("#Zażółć") == "zażółć"
    assert normalize("client-a/sub_task") == "client-a/sub_task"


def test_segments():
    assert segments("work/projects/client-a") == ["work", "projects", "client-a"]
    assert segments("") == []


def test_ancestors_empty_path():
    assert ancestors("") == []


def test_ancestors_includes_self_top_down():
    assert ancestors("work/projects/client-a") == [
        "work",
        "work/projects",
        "work/projects/client-a",
    ]
    assert ancestors("work") == ["work"]


def test_extract_basic_and_hierarchical():
    assert extract_inline_tags("see #work and #work/projects/client-a here") == {
        "work",
        "work/projects/client-a",
    }


def test_extract_lowercases():
    assert extract_inline_tags("#Work/Projects") == {"work/projects"}


def test_extract_ignores_code_span_and_fence():
    body = "text `#nope` more\n\n```\n#alsonope\n```\n\n#yes"
    assert extract_inline_tags(body) == {"yes"}


def test_extract_skips_atx_heading_marker():
    # '# Heading' is a heading (marker consumed); '#tag' (no space) is a tag.
    assert extract_inline_tags("# Heading\n\n#realtag") == {"realtag"}


def test_extract_requires_boundary_before_hash():
    # no tag inside words / urls: 'C#', 'a#b', '.../#anchor'
    assert extract_inline_tags("C# and a#b and http://x/#anchor") == set()


def test_extract_stops_at_punctuation():
    assert extract_inline_tags("end of #work. next") == {"work"}


def test_extract_trailing_slash_normalized():
    assert extract_inline_tags("#work/") == {"work"}
