from unittest.mock import Mock

from kajet_turbo.models import Note
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.notes.locator import locate_many


def _note(note_id: str, title: str) -> Note:
    return Note(
        id=note_id,
        workspace="ws",
        owner_id="u1",
        title=title,
        folder="",
        created_at="2026-01-01",
        updated_at="2026-01-01",
    )


def test_locate_many_trims_ids_and_attaches_head_shas_only_to_existing_files(tmp_path):
    existing = _note("n1", "Existing")
    missing_file = _note("n2", "Missing")
    (tmp_path / "Existing.md").touch()
    repo = Mock(spec=NoteRepository)
    repo.get_many.return_value = [existing, missing_file]
    git_repo = Mock(spec=GitRepository)
    git_repo.head_shas_for_paths.return_value = {"Existing.md": "abc123"}

    located = locate_many(
        repo,
        [" n1 ", "", "  ", "missing-row", "n2"],
        "u1",
        str(tmp_path),
        git_repo,
    )

    repo.get_many.assert_called_once_with(["n1", "missing-row", "n2"], "u1")
    git_repo.head_shas_for_paths.assert_called_once_with(["Existing.md"])
    assert set(located) == {"n1", "n2"}
    assert located["n1"].file_exists is True
    assert located["n1"].head_sha == "abc123"
    assert located["n2"].file_exists is False
    assert located["n2"].head_sha is None
