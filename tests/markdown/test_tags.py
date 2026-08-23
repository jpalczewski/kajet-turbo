from kajet_turbo.markdown import (
    ancestors,
    extract_inline_tags,
    normalize,
    remap_path,
    rewrite_inline_tags,
    segments,
)


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


def test_extract_unicode_tag():
    assert extract_inline_tags("notatka #zażółć tutaj") == {"zażółć"}


def test_extract_hyphen_before_hash_is_boundary():
    # Hyphen before '#' is a boundary (tag captured); underscore is not.
    assert extract_inline_tags("foo-#bar") == {"bar"}
    assert extract_inline_tags("foo_#bar") == set()


def _work_to_job(tag: str) -> str | None:
    """The remapper a 'work' -> 'job' rename installs, built the way the service builds it."""
    return remap_path(tag, "work", "job")


def test_rewrite_moves_tag_and_its_subtree():
    assert rewrite_inline_tags("#work and #work/projects", _work_to_job) == (
        "#job and #job/projects",
        True,
    )


def test_rewrite_stops_at_segment_boundary():
    assert rewrite_inline_tags("#workflow stays", _work_to_job) == ("#workflow stays", False)


def test_rewrite_leaves_untouched_tags_alone():
    assert rewrite_inline_tags("#other and #work", _work_to_job) == ("#other and #job", True)


def test_rewrite_honours_the_same_word_boundary_as_extraction():
    body = "C# and a#b and http://x/#work"
    assert rewrite_inline_tags(body, _work_to_job) == (body, False)
    assert rewrite_inline_tags("foo-#work", _work_to_job)[0] == "foo-#job"
    assert rewrite_inline_tags("foo_#work", _work_to_job)[0] == "foo_#work"


def test_rewrite_matches_unicode_tags():
    assert rewrite_inline_tags("notatka #zażółć tutaj", lambda t: "gesla") == (
        "notatka #gesla tutaj",
        True,
    )


def test_rewrite_canonicalizes_only_the_tags_it_moves():
    # '#Work' normalizes to 'work', so it moves — and comes back canonical.
    assert rewrite_inline_tags("#Work and #Other", _work_to_job) == ("#job and #Other", True)


def test_rewrite_also_touches_code_spans():
    # Pins the documented compromise: this works on raw text, unlike extract_inline_tags.
    assert extract_inline_tags("`#work`") == set()
    assert rewrite_inline_tags("`#work`", _work_to_job) == ("`#job`", True)


def test_remap_path_matches_on_segment_boundaries():
    assert remap_path("work", "work", "job") == "job"
    assert remap_path("work/projects", "work", "job") == "job/projects"
    assert remap_path("workflow", "work", "job") is None
    assert remap_path("homework", "work", "job") is None
