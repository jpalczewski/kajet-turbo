"""delete_many() batch coverage for NoteService."""

import pytest

from kajet_turbo.markdown import Chunk
from kajet_turbo.repositories.git import GitRepository
from tests.services.helpers import head_sha


def test_delete_many_applies_all_in_one_commit(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", [])
    sha1 = head_sha(workspace, "First.md")
    sha2 = head_sha(workspace, "Second.md")

    result = service.delete_many(
        "u1",
        "ws",
        str(workspace),
        [
            {"note_id": r1["note_id"], "expected_sha": sha1},
            {"note_id": r2["note_id"], "expected_sha": sha2},
        ],
    )

    assert result["applied"] is True
    assert [r["note_id"] for r in result["results"]] == [r1["note_id"], r2["note_id"]]
    assert service.get_with_content(r1["note_id"], "u1", str(workspace)) is None
    assert service.get_with_content(r2["note_id"], "u1", str(workspace)) is None
    history = GitRepository(str(workspace)).file_history("First.md")
    assert history[0]["message"].startswith("note: delete 2 notes")


def test_delete_many_stale_sha_rejects_whole_batch(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", [])
    sha1 = head_sha(workspace, "First.md")

    result = service.delete_many(
        "u1",
        "ws",
        str(workspace),
        [
            {"note_id": r1["note_id"], "expected_sha": sha1},
            {"note_id": r2["note_id"], "expected_sha": "0" * 40},
        ],
    )

    assert result["applied"] is False
    assert result["errors"][0]["index"] == 1
    assert "current_sha" not in result["errors"][0]
    # nothing deleted, including the valid first item
    assert service.get_with_content(r1["note_id"], "u1", str(workspace)) is not None
    assert service.get_with_content(r2["note_id"], "u1", str(workspace)) is not None


def test_delete_many_missing_note_rejects_batch(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    sha1 = head_sha(workspace, "First.md")

    result = service.delete_many(
        "u1",
        "ws",
        str(workspace),
        [
            {"note_id": r1["note_id"], "expected_sha": sha1},
            {"note_id": "does-not-exist", "expected_sha": "irrelevant"},
        ],
    )

    assert result["applied"] is False
    assert service.get_with_content(r1["note_id"], "u1", str(workspace)) is not None


def test_delete_many_rejects_duplicate_note_id(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    sha1 = head_sha(workspace, "First.md")

    result = service.delete_many(
        "u1",
        "ws",
        str(workspace),
        [
            {"note_id": r1["note_id"], "expected_sha": sha1},
            {"note_id": r1["note_id"], "expected_sha": sha1},
        ],
    )

    assert result["applied"] is False
    assert "Duplikat" in result["errors"][0]["error"]


def test_delete_many_requires_expected_sha(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])

    result = service.delete_many("u1", "ws", str(workspace), [{"note_id": r1["note_id"]}])

    assert result["applied"] is False
    assert service.get_with_content(r1["note_id"], "u1", str(workspace)) is not None


def test_delete_many_accepts_shortened_sha(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", [])
    short_sha = head_sha(workspace, "First.md")[:10]

    result = service.delete_many(
        "u1", "ws", str(workspace), [{"note_id": r1["note_id"], "expected_sha": short_sha}]
    )

    assert result["applied"] is True
    assert service.get_with_content(r1["note_id"], "u1", str(workspace)) is None


def test_delete_many_empty_batch_raises(service, workspace):
    with pytest.raises(ValueError):
        service.delete_many("u1", "ws", str(workspace), [])


def test_delete_many_clears_tags_links_and_index(service, workspace):
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", [])
    r1 = service.save("u1", "ws", str(workspace), "First", "links [[Second]]\n", ["tag-a"])
    sha1 = head_sha(workspace, "First.md")

    result = service.delete_many(
        "u1", "ws", str(workspace), [{"note_id": r1["note_id"], "expected_sha": sha1}]
    )

    assert result["applied"] is True
    assert service.backlinks(r2["note_id"], "u1") == []
    assert service.get_with_content(r2["note_id"], "u1", str(workspace)) is not None


def test_delete_many_sweeps_orphan_tags_once_for_the_whole_batch(service, workspace):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", ["shared", "only-first"])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", ["shared"])
    r3 = service.save("u1", "ws", str(workspace), "Third", "three\n", ["shared"])
    sha1 = head_sha(workspace, "First.md")
    sha2 = head_sha(workspace, "Second.md")

    result = service.delete_many(
        "u1",
        "ws",
        str(workspace),
        [
            {"note_id": r1["note_id"], "expected_sha": sha1},
            {"note_id": r2["note_id"], "expected_sha": sha2},
        ],
    )

    assert result["applied"] is True
    paths = {row["path"] for row in service._tag_repo.tag_tree("ws", "u1")}
    # "only-first" had no other holder and is gone; "shared" survives via r3.
    assert "only-first" not in paths
    assert "shared" in paths
    assert service.get_with_content(r3["note_id"], "u1", str(workspace)) is not None


def test_delete_many_calls_sweep_orphan_tags_once_not_per_note(service, workspace, monkeypatch):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", ["tag-a"])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", ["tag-b"])
    r3 = service.save("u1", "ws", str(workspace), "Third", "three\n", ["tag-c"])
    sha1 = head_sha(workspace, "First.md")
    sha2 = head_sha(workspace, "Second.md")
    sha3 = head_sha(workspace, "Third.md")
    calls = 0
    original = service._tag_repo.sweep_orphan_tags_in_session

    def counting_sweep(session, workspace_name, owner_id):
        nonlocal calls
        calls += 1
        return original(session, workspace_name, owner_id)

    monkeypatch.setattr(service._tag_repo, "sweep_orphan_tags_in_session", counting_sweep)

    result = service.delete_many(
        "u1",
        "ws",
        str(workspace),
        [
            {"note_id": r1["note_id"], "expected_sha": sha1},
            {"note_id": r2["note_id"], "expected_sha": sha2},
            {"note_id": r3["note_id"], "expected_sha": sha3},
        ],
    )

    assert result["applied"] is True
    assert calls == 1


def test_delete_clears_chunks_without_an_indexer(service, workspace):
    result = service.save("u1", "ws", str(workspace), "First", "body\n", [])
    note_id = result["note_id"]
    service._chunk_repo.replace_chunks(
        note_id, "ws", "u1", "First", [Chunk(0, ["# First"], "body", 0, 4)], None, None
    )
    service._indexer = None

    service.delete(note_id, "u1", str(workspace))

    assert service._crud_repo.get(note_id, owner_id="u1") is None
    assert service._chunk_repo.get_chunks(note_id) == []


def test_delete_many_rolls_back_database_teardowns(service, workspace, monkeypatch):
    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", ["first"])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", ["second"])
    sha1 = head_sha(workspace, "First.md")
    sha2 = head_sha(workspace, "Second.md")
    original = service._link_repo.delete_links_to_in_session

    def fail_on_second(session, note_id):
        if note_id == r2["note_id"]:
            raise RuntimeError("injected teardown failure")
        original(session, note_id)

    monkeypatch.setattr(service._link_repo, "delete_links_to_in_session", fail_on_second)

    with pytest.raises(RuntimeError, match="injected teardown failure"):
        service.delete_many(
            "u1",
            "ws",
            str(workspace),
            [
                {"note_id": r1["note_id"], "expected_sha": sha1},
                {"note_id": r2["note_id"], "expected_sha": sha2},
            ],
        )

    assert service._crud_repo.get(r1["note_id"], owner_id="u1") is not None
    assert service._crud_repo.get(r2["note_id"], owner_id="u1") is not None
    assert (workspace / "First.md").exists()
    assert (workspace / "Second.md").exists()
    assert head_sha(workspace, "First.md") == sha1
    assert head_sha(workspace, "Second.md") == sha2
    assert (
        service.get_with_content(r1["note_id"], owner_id="u1", ws_path=str(workspace)) is not None
    )
    assert (
        service.get_with_content(r2["note_id"], owner_id="u1", ws_path=str(workspace)) is not None
    )


def test_delete_many_git_failure_rolls_back_database_teardowns(service, workspace):
    from unittest.mock import patch

    from kajet_turbo.repositories.git import GitError

    r1 = service.save("u1", "ws", str(workspace), "First", "one\n", ["first"])
    r2 = service.save("u1", "ws", str(workspace), "Second", "two\n", ["second"])
    sha1 = head_sha(workspace, "First.md")
    sha2 = head_sha(workspace, "Second.md")

    # delete_files is mocked out entirely, so it never touches the filesystem — the only
    # thing this test can prove is that the row teardowns rolled back with it.
    with (
        patch(
            "kajet_turbo.repositories.git.GitRepository.delete_files", side_effect=GitError("fail")
        ),
        pytest.raises(GitError),
    ):
        service.delete_many(
            "u1",
            "ws",
            str(workspace),
            [
                {"note_id": r1["note_id"], "expected_sha": sha1},
                {"note_id": r2["note_id"], "expected_sha": sha2},
            ],
        )

    assert service._crud_repo.get(r1["note_id"], owner_id="u1") is not None
    assert service._crud_repo.get(r2["note_id"], owner_id="u1") is not None
