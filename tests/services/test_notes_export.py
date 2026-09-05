"""export_folder() coverage for NoteService."""

from tests.services.conftest import workspace_target


def test_export_folder_concatenates_notes_with_headings(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "01 First", "first body", [], folder="a")
    service.save(
        workspace_target("u1", "ws", workspace), "02 Second", "second body", [], folder="a"
    )
    result = service.export_folder("ws", "u1", str(workspace), "a")
    assert result["note_count"] == 2
    assert "first body" in result["markdown"]
    assert "second body" in result["markdown"]
    assert result["markdown"].index("first body") < result["markdown"].index("second body")
    assert result["truncated"] is False
    assert result["omitted"] == []


def test_export_folder_includes_subtree(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "Nested", "nested body", [], folder="a/b")
    result = service.export_folder("ws", "u1", str(workspace), "a")
    assert result["note_count"] == 1
    assert "nested body" in result["markdown"]


def test_export_folder_excludes_sibling_folders(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "In", "in body", [], folder="a")
    service.save(workspace_target("u1", "ws", workspace), "Out", "out body", [], folder="b")
    result = service.export_folder("ws", "u1", str(workspace), "a")
    assert result["note_count"] == 1
    assert "out body" not in result["markdown"]


def test_export_folder_truncates_at_note_boundary(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "01 First", "x" * 50, [], folder="a")
    service.save(workspace_target("u1", "ws", workspace), "02 Second", "y" * 50, [], folder="a")
    result = service.export_folder("ws", "u1", str(workspace), "a", max_chars=60)
    assert result["note_count"] == 1
    assert "x" * 50 in result["markdown"]
    assert "y" * 50 not in result["markdown"]
    assert result["truncated"] is True
    assert len(result["omitted"]) == 1
    assert result["omitted"][0]["title"] == "02 Second"


def test_export_folder_always_includes_first_note_even_if_oversized(service, workspace):
    service.save(workspace_target("u1", "ws", workspace), "01 Huge", "z" * 200, [], folder="a")
    result = service.export_folder("ws", "u1", str(workspace), "a", max_chars=10)
    assert result["note_count"] == 1
    assert "z" * 200 in result["markdown"]


def test_export_folder_empty_folder_returns_empty(service, workspace):
    result = service.export_folder("ws", "u1", str(workspace), "does-not-exist")
    assert result == {
        "markdown": "",
        "note_count": 0,
        "total_chars": 0,
        "truncated": False,
        "omitted": [],
    }
