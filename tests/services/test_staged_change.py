"""Unit tests for the StagedChange/staged_workspace_change primitive itself.

Every current caller exercises this indirectly through a service method; these tests
pin the contract directly against a real GitRepository so a regression here doesn't
have to be diagnosed through five layers of service code first.
"""

from functools import partial
from pathlib import Path

import pytest

from kajet_turbo.repositories.git import GitError, GitRepository
from kajet_turbo.services.notes import staged_change as staged_change_module
from kajet_turbo.services.notes.staged_change import StagedChange, staged_workspace_change


def test_mid_batch_oserror_restores_earlier_items_byte_for_byte(workspace):
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

    with (
        pytest.raises(OSError, match="disk full"),
        staged_workspace_change(repo, items, "note: batch edit"),
    ):
        pass

    assert (workspace / "a.md").read_bytes() == original_a
    assert (workspace / "b.md").read_bytes() == original_b


def test_rename_shaped_item_rolls_back_to_old_path_on_failure(workspace, monkeypatch):
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

    with (
        pytest.raises(GitError, match="commit failed"),
        staged_workspace_change(repo, [item], "note: rename"),
    ):
        pass

    assert old_path.exists()
    assert old_path.read_text() == "content"
    assert not new_path.exists()


def test_delete_shaped_item_rolls_back_on_failure(workspace, monkeypatch):
    path = workspace / "note.md"
    path.write_text("content")
    repo = GitRepository(str(workspace))
    repo.commit_file("note.md", "note: seed")

    def boom(self, **kwargs) -> None:
        raise GitError("commit failed")

    monkeypatch.setattr(type(repo), "commit_changes", boom)
    item = StagedChange(add=None, remove="note.md", apply=path.unlink)

    with (
        pytest.raises(GitError, match="commit failed"),
        staged_workspace_change(repo, [item], "note: delete"),
    ):
        pass

    assert path.exists()
    assert path.read_text() == "content"


def test_known_bytes_skips_the_snapshot_read_but_restore_still_works(workspace, monkeypatch):
    known_path = workspace / "known.md"
    other_path = workspace / "other.md"
    known_path.write_text("known original")
    other_path.write_text("other original")
    repo = GitRepository(str(workspace))
    repo.commit_files(["known.md", "other.md"], "note: seed")

    read_calls: list[str] = []
    real_read_bytes = Path.read_bytes

    class TrackingPath(Path):
        def read_bytes(self):
            read_calls.append(str(self))
            return real_read_bytes(self)

    monkeypatch.setattr(staged_change_module, "Path", TrackingPath)

    def boom():
        raise OSError("disk full")

    items = [
        StagedChange(
            add="known.md",
            remove=None,
            apply=partial(known_path.write_text, "changed known"),
            known_bytes=b"known original",
        ),
        StagedChange(add="other.md", remove=None, apply=boom),
    ]

    with (
        pytest.raises(OSError, match="disk full"),
        staged_workspace_change(repo, items, "note: batch edit"),
    ):
        pass

    assert any("other.md" in call for call in read_calls)  # tracking actually works
    assert not any("known.md" in call for call in read_calls)
    assert known_path.read_bytes() == b"known original"
    assert other_path.read_bytes() == b"other original"


def test_pure_rename_skips_byte_snapshot_and_restores_via_rename(workspace, monkeypatch):
    old_path = workspace / "old.md"
    new_path = workspace / "new.md"
    old_path.write_text("content")
    repo = GitRepository(str(workspace))
    repo.commit_file("old.md", "note: seed")

    class NoReadPath(Path):
        def read_bytes(self):
            raise AssertionError("a pure_rename item must never be byte-snapshotted")

    monkeypatch.setattr(staged_change_module, "Path", NoReadPath)

    def apply() -> None:
        old_path.rename(new_path)

    def boom(self, **kwargs) -> None:
        raise GitError("commit failed")

    monkeypatch.setattr(type(repo), "commit_changes", boom)
    item = StagedChange(add="new.md", remove="old.md", apply=apply, pure_rename=True)

    with (
        pytest.raises(GitError, match="commit failed"),
        staged_workspace_change(repo, [item], "note: rename"),
    ):
        pass

    assert old_path.read_text() == "content"
    assert not new_path.exists()


def test_pure_rename_guard_does_not_clobber_pre_existing_target_on_restore(workspace):
    """A naive "if add exists, rename it onto remove" restore is a data-loss bug: if
    apply() collided with an unrelated file already at the target (and, like move()'s
    apply_move, already put `remove` back itself before raising), that unrelated file
    still sits at `add` when restore runs. Blindly renaming it onto `remove` would
    overwrite the note being moved with the stranger's content."""
    old_path = workspace / "old.md"
    new_path = workspace / "new.md"
    old_path.write_text("mover content")
    new_path.write_text("stranger content")
    repo = GitRepository(str(workspace))
    repo.commit_files(["old.md", "new.md"], "note: seed")

    def apply() -> None:
        tmp_path = old_path.parent / ".tmp-rename"
        old_path.rename(tmp_path)
        if new_path.exists():
            tmp_path.rename(old_path)
            raise FileExistsError("target already exists")
        tmp_path.rename(new_path)

    item = StagedChange(add="new.md", remove="old.md", apply=apply, pure_rename=True)

    with (
        pytest.raises(FileExistsError),
        staged_workspace_change(repo, [item], "note: rename"),
    ):
        pass

    assert old_path.read_text() == "mover content"
    assert new_path.read_text() == "stranger content"


def test_pure_rename_requires_both_add_and_remove():
    with pytest.raises(ValueError, match="pure_rename requires both add and remove"):
        StagedChange(add="a.md", remove=None, apply=lambda: None, pure_rename=True)


def test_pure_rename_and_known_bytes_are_mutually_exclusive():
    with pytest.raises(ValueError, match="mutually exclusive"):
        StagedChange(
            add="a.md",
            remove="b.md",
            apply=lambda: None,
            pure_rename=True,
            known_bytes=b"x",
        )
