import pytest
from pathlib import Path
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.workspace import (
    list_workspaces,
    create_workspace,
    note_filepath,
    write_note_file,
    read_note_file,
    title_to_windows_filename,
    normalize_folder,
    scan_notes,
)


@pytest.fixture
def workspaces_dir(tmp_path):
    return tmp_path / "workspaces"


@pytest.fixture
def workspace(workspaces_dir):
    ws = workspaces_dir / "moj-projekt"
    ws.mkdir(parents=True)
    GitRepository.init(str(ws))
    return ws


def test_list_workspaces(workspace, workspaces_dir):
    (workspaces_dir / "drugi-projekt").mkdir()
    names = list_workspaces(str(workspaces_dir))
    assert "moj-projekt" in names
    assert "drugi-projekt" in names


def test_list_workspaces_with_user_id(tmp_path):
    user_dir = tmp_path / "u1"
    (user_dir / "ws-a").mkdir(parents=True)
    (user_dir / "ws-b").mkdir()
    names = list_workspaces(str(tmp_path), user_id="u1")
    assert set(names) == {"ws-a", "ws-b"}


# --- title_to_windows_filename ---

def test_title_to_windows_filename_strips_colon():
    assert title_to_windows_filename("Spotkanie: kickoff") == "Spotkanie kickoff"


def test_title_to_windows_filename_strips_all_forbidden():
    assert title_to_windows_filename('a"b*c?d<e>f|g') == "a b c d e f g"


def test_title_to_windows_filename_normalizes_multiple_spaces():
    assert title_to_windows_filename("a:::b") == "a b"


def test_title_to_windows_filename_keeps_unicode():
    assert title_to_windows_filename("Żółta łódź") == "Żółta łódź"


def test_title_to_windows_filename_reserved_con():
    assert title_to_windows_filename("CON") == "_CON"


def test_title_to_windows_filename_reserved_case_insensitive():
    assert title_to_windows_filename("nul") == "_nul"


def test_title_to_windows_filename_reserved_com1():
    assert title_to_windows_filename("COM1") == "_COM1"


def test_title_to_windows_filename_empty_returns_untitled():
    assert title_to_windows_filename("") == "untitled"


def test_title_to_windows_filename_all_forbidden_returns_untitled():
    assert title_to_windows_filename(":::") == "untitled"


def test_title_to_windows_filename_strips_trailing_dot():
    assert title_to_windows_filename("file.") == "file"


def test_title_to_windows_filename_truncates_to_200():
    assert len(title_to_windows_filename("a" * 300)) == 200


# --- normalize_folder ---

def test_normalize_folder_empty():
    assert normalize_folder("") == ""


def test_normalize_folder_basic():
    assert normalize_folder("Projekty/Klient A") == "Projekty/Klient A"


def test_normalize_folder_strips_leading_trailing_slash():
    assert normalize_folder("/foo/bar/") == "foo/bar"


def test_normalize_folder_strips_spaces():
    assert normalize_folder("  foo/bar  ") == "foo/bar"


def test_normalize_folder_rejects_dotdot():
    with pytest.raises(ValueError):
        normalize_folder("../etc")


def test_normalize_folder_rejects_dotdot_nested():
    with pytest.raises(ValueError):
        normalize_folder("foo/../bar")


def test_normalize_folder_sanitizes_forbidden_chars_in_segment():
    result = normalize_folder("Proj:ekt/Klient")
    assert result == "Proj ekt/Klient"


# --- note_filepath ---

def test_note_filepath_root():
    path = note_filepath("/ws", "", "My Note")
    assert path == str(Path("/ws/My Note.md"))


def test_note_filepath_with_folder():
    path = note_filepath("/ws", "Projekty/Klient A", "Spotkanie")
    assert path == str(Path("/ws/Projekty/Klient A/Spotkanie.md"))


def test_note_filepath_sanitizes_title():
    path = note_filepath("/ws", "", "Spotkanie: kickoff")
    assert path == str(Path("/ws/Spotkanie kickoff.md"))


# --- write/read ---

def test_write_and_read_note_file(workspace):
    path = note_filepath(str(workspace), "", "Test Note")
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


def test_scan_notes_finds_all_including_subfolders(workspace):
    for i in range(2):
        path = note_filepath(str(workspace), "", f"Notatka {i}")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        write_note_file(path, f"id{i}", f"Notatka {i}", [], "2026-06-08T12:00:00+00:00", "2026-06-08T12:00:00+00:00", f"treść {i}")
    path_sub = note_filepath(str(workspace), "Projekty", "Sub-notatka")
    Path(path_sub).parent.mkdir(parents=True, exist_ok=True)
    write_note_file(path_sub, "idsub", "Sub-notatka", [], "2026-06-08T12:00:00+00:00", "2026-06-08T12:00:00+00:00", "sub")
    notes = scan_notes(str(workspace))
    ids = [n["id"] for n in notes if n["id"]]
    assert set(ids) == {"id0", "id1", "idsub"}


def test_scan_notes_ignores_non_note_md(workspace):
    (workspace / "README.md").write_text("# Readme\n\nNo frontmatter here.")
    path = note_filepath(str(workspace), "", "Real Note")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    write_note_file(path, "r1", "Real Note", [], "2026-06-08T12:00:00+00:00", "2026-06-08T12:00:00+00:00", "content")
    notes = scan_notes(str(workspace))
    ids = [n["id"] for n in notes if n["id"]]
    assert ids == ["r1"]


def test_create_workspace(tmp_path):
    ws_path = create_workspace("nowy-projekt", str(tmp_path))
    assert (tmp_path / "nowy-projekt").is_dir()
    assert (tmp_path / "nowy-projekt" / ".git").is_dir()
    assert ws_path == str(tmp_path / "nowy-projekt")


def test_create_workspace_with_user_id(tmp_path):
    ws_path = create_workspace("moj-ws", str(tmp_path), user_id="u42")
    assert (tmp_path / "u42" / "moj-ws" / ".git").is_dir()
    assert ws_path == str(tmp_path / "u42" / "moj-ws")


def test_create_workspace_rejects_invalid_name(tmp_path):
    with pytest.raises(ValueError):
        create_workspace("foo/bar", str(tmp_path))
    with pytest.raises(ValueError):
        create_workspace("", str(tmp_path))


def test_create_workspace_rejects_duplicate(tmp_path):
    create_workspace("duplikat", str(tmp_path))
    with pytest.raises(FileExistsError):
        create_workspace("duplikat", str(tmp_path))


def test_rename_file_commit(workspace):
    repo = GitRepository(str(workspace))

    initial = workspace / "hello.md"
    initial.write_text("content")
    repo.commit_file("hello.md", "add hello")

    repo.rename_file("hello.md", "world.md", "rename hello to world")

    assert not initial.exists()
    assert (workspace / "world.md").exists()
    assert (workspace / "world.md").read_text() == "content"
