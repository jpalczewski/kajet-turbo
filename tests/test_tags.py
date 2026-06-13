from kajet_turbo.tags import ancestors, normalize, segments


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
