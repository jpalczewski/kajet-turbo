"""Unit tests for the StagedChange/staged_workspace_change primitive itself.

Every current caller exercises this indirectly through a service method; these tests
pin the contract directly against a real GitRepository so a regression here doesn't
have to be diagnosed through five layers of service code first.
"""

from functools import partial

import pytest

from kajet_turbo.repositories.git import GitError, GitRepository
from kajet_turbo.services.notes.staged_change import StagedChange, staged_workspace_change


def test_mid_batch_oserror_restores_earlier_items_byte_for_byte(git_workspace_factory):
    workspace = git_workspace_factory()
    (workspace / "a.md").write_text("original a")
    (workspace / "b.md").write_text("original b")
    repo = GitRepository(str(workspace))
    repo.commit_files(["a.md", "b.md"], "note: seed")
    original_a = (workspace / "a.md").read_bytes()
    original_b = (workspace / "b.md").read_bytes()

    def boom():
        raise OSError("disk full")

    items = [
        StagedChange(
            add="a.md", remove=None, apply=partial((workspace / "a.md").write_text, "changed a")
        ),
        StagedChange(add="b.md", remove=None, apply=boom),
    ]

    with pytest.raises(OSError), staged_workspace_change(repo, items, "note: batch edit"):
        pass

    assert (workspace / "a.md").read_bytes() == original_a
    assert (workspace / "b.md").read_bytes() == original_b


def test_rename_shaped_item_rolls_back_to_old_path_on_failure(git_workspace_factory, monkeypatch):
    workspace = git_workspace_factory()
    old_path = workspace / "old.md"
    new_path = workspace / "new.md"
    old_path.write_text("content")
    repo = GitRepository(str(workspace))
    repo.commit_file("old.md", "note: seed")

    def apply() -> None:
        new_path.write_text("content")
        old_path.unlink()

    def boom(self, **kwargs) -> None:
        raise GitError("commit failed")

    monkeypatch.setattr(type(repo), "commit_changes", boom)
    item = StagedChange(add="new.md", remove="old.md", apply=apply)

    with pytest.raises(GitError), staged_workspace_change(repo, [item], "note: rename"):
        pass

    assert old_path.exists()
    assert old_path.read_text() == "content"
    assert not new_path.exists()


def test_delete_shaped_item_rolls_back_on_failure(git_workspace_factory, monkeypatch):
    workspace = git_workspace_factory()
    path = workspace / "note.md"
    path.write_text("content")
    repo = GitRepository(str(workspace))
    repo.commit_file("note.md", "note: seed")

    def boom(self, **kwargs) -> None:
        raise GitError("commit failed")

    monkeypatch.setattr(type(repo), "commit_changes", boom)
    item = StagedChange(add=None, remove="note.md", apply=path.unlink)

    with pytest.raises(GitError), staged_workspace_change(repo, [item], "note: delete"):
        pass

    assert path.exists()
    assert path.read_text() == "content"
