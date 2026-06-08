import subprocess
import pytest
from pathlib import Path
from kajet_turbo.workspace import (
    list_workspaces,
    note_filepath,
    write_note_file,
    read_note_file,
    title_to_slug,
    scan_notes,
)


@pytest.fixture
def workspaces_dir(tmp_path):
    return tmp_path / "workspaces"


@pytest.fixture
def workspace(workspaces_dir):
    ws = workspaces_dir / "moj-projekt"
    ws.mkdir(parents=True)
    (ws / "notes").mkdir()
    subprocess.run(["git", "init", str(ws)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(ws), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(ws), check=True, capture_output=True)
    return ws


def test_list_workspaces(workspace, workspaces_dir):
    (workspaces_dir / "drugi-projekt").mkdir()
    names = list_workspaces(str(workspaces_dir))
    assert "moj-projekt" in names
    assert "drugi-projekt" in names


def test_title_to_slug():
    assert title_to_slug("Python async programming!") == "python-async-programming"
    assert title_to_slug("Żółta łódź") == "ta-d"  # non-ascii stripped


def test_note_filepath(workspace):
    path = note_filepath(str(workspace), "abc1234", "My Note")
    assert path.endswith("abc1234-my-note.md")
    assert "notes" in path


def test_write_and_read_note_file(workspace):
    path = note_filepath(str(workspace), "abc1234", "Test Note")
    write_note_file(
        path,
        note_id="abc1234",
        title="Test Note",
        tags=["python"],
        created_at="2026-06-08T12:00:00+00:00",
        updated_at="2026-06-08T12:00:00+00:00",
        content="# Hello\n\nTreść notatki.",
    )
    result = read_note_file(path)
    assert result["id"] == "abc1234"
    assert result["title"] == "Test Note"
    assert result["tags"] == ["python"]
    assert "Treść notatki" in result["content"]


def test_scan_notes_finds_all_files(workspace):
    for i in range(3):
        path = note_filepath(str(workspace), f"id{i}", f"Notatka {i}")
        write_note_file(path, f"id{i}", f"Notatka {i}", [], "2026-06-08T12:00:00+00:00", "2026-06-08T12:00:00+00:00", f"treść {i}")
    notes = scan_notes(str(workspace))
    assert len(notes) == 3
