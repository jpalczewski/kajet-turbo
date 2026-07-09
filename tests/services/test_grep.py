import pytest


def test_grep_finds_literal_match_with_line_number(service, workspace):
    service.save("u1", "ws", str(workspace), "Notes", "line one\nmatch here\nline three\n", [])
    result = service.grep("ws", str(workspace), "match here")
    assert result["truncated"] is False
    assert len(result["matches"]) == 1
    m = result["matches"][0]
    assert m["title"] == "Notes"
    assert m["line_number"] == 2
    assert m["line"] == "match here"


def test_grep_is_case_insensitive_by_default(service, workspace):
    service.save("u1", "ws", str(workspace), "Notes", "MAFIOSO appears here\n", [])
    result = service.grep("ws", str(workspace), "mafioso")
    assert len(result["matches"]) == 1


def test_grep_case_sensitive_excludes_different_case(service, workspace):
    service.save("u1", "ws", str(workspace), "Notes", "MAFIOSO appears here\n", [])
    result = service.grep("ws", str(workspace), "mafioso", case_sensitive=True)
    assert result["matches"] == []


def test_grep_matches_frontmatter_tags(service, workspace):
    service.save("u1", "ws", str(workspace), "Notes", "unrelated body\n", ["angelika"])
    result = service.grep("ws", str(workspace), "angelika")
    assert len(result["matches"]) == 1
    assert result["matches"][0]["title"] == "Notes"


def test_grep_multiple_matches_in_one_note(service, workspace):
    service.save("u1", "ws", str(workspace), "Notes", "foo\nfoo again\nbar\nfoo once more\n", [])
    result = service.grep("ws", str(workspace), "foo")
    assert [m["line_number"] for m in result["matches"]] == [1, 2, 4]


def test_grep_scoped_to_folder_subtree(service, workspace):
    service.save("u1", "ws", str(workspace), "In scope", "needle here\n", [], folder="a/b")
    service.save("u1", "ws", str(workspace), "Out of scope", "needle here\n", [], folder="c")
    result = service.grep("ws", str(workspace), "needle", folder="a")
    assert [m["title"] for m in result["matches"]] == ["In scope"]


def test_grep_respects_max_results_and_sets_truncated(service, workspace):
    service.save(
        "u1", "ws", str(workspace), "Notes", "\n".join(f"needle {i}" for i in range(10)), []
    )
    result = service.grep("ws", str(workspace), "needle", max_results=3)
    assert len(result["matches"]) == 3
    assert result["truncated"] is True


def test_grep_blank_pattern_raises(service, workspace):
    with pytest.raises(ValueError):
        service.grep("ws", str(workspace), "   ")
