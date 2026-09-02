"""Write-verb coverage for CollectionService: add/redefine/delete policies."""

import pytest

from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.services.collections import CollectionService


@pytest.fixture
def collections(service) -> CollectionService:
    return CollectionService(service._crud_repo)


def _head_sha(workspace):
    return GitRepository(str(workspace)).file_history(".kajet/collections.yaml", limit=1)[0]["sha"]


def test_define_collection_adds_and_commits(collections, workspace):
    result = collections.define_collection(
        str(workspace), "ws", "u1", "weekly", "week", "one", "weekly/{year}", "{key}"
    )

    assert result["verb"] == "add"
    assert result["affected_count"] == 0
    loaded = collections.list_collections(str(workspace))
    assert loaded["weekly"].folder == "weekly/{year}"
    history = GitRepository(str(workspace)).file_history(".kajet/collections.yaml")
    assert history[0]["message"] == "collections: add weekly"


def test_define_collection_redefine_reports_affected_without_moving_notes(
    collections, service, workspace
):
    collections.define_collection(
        str(workspace), "ws", "u1", "weekly", "week", "one", "weekly/{year}", "{key}"
    )
    saved = service.save(
        "u1", "ws", str(workspace), "2026-W23", "content\n", [], folder="weekly/2026"
    )
    before = service.get_with_content(saved["note_id"], "u1", str(workspace))

    result = collections.define_collection(
        str(workspace), "ws", "u1", "weekly", "week", "one", "weekly-v2/{year}", "{key}"
    )

    assert result["verb"] == "update"
    assert result["affected_count"] == 1
    assert result["dropped"] == [{"folder": "weekly/2026", "title": "2026-W23"}]
    after = service.get_with_content(saved["note_id"], "u1", str(workspace))
    assert after.folder == before.folder
    assert after.content == before.content


def test_define_collection_dry_run_writes_nothing(collections, workspace):
    collections.define_collection(
        str(workspace), "ws", "u1", "weekly", "week", "one", "weekly/{year}", "{key}"
    )
    sha_before = _head_sha(workspace)
    text_before = (workspace / ".kajet" / "collections.yaml").read_text()

    result = collections.define_collection(
        str(workspace),
        "ws",
        "u1",
        "weekly",
        "week",
        "one",
        "weekly-v2/{year}",
        "{key}",
        dry_run=True,
    )

    assert result["would_write"] is True
    assert _head_sha(workspace) == sha_before
    assert (workspace / ".kajet" / "collections.yaml").read_text() == text_before
    # the on-disk definition is still the original pattern
    assert collections.list_collections(str(workspace))["weekly"].folder == "weekly/{year}"


def test_define_collection_rejects_colliding_folder_pattern(collections, workspace):
    collections.define_collection(
        str(workspace), "ws", "u1", "weekly", "week", "one", "archive/{year}", "{key}"
    )
    sha_before = _head_sha(workspace)
    text_before = (workspace / ".kajet" / "collections.yaml").read_text()

    with pytest.raises(ValueError, match="would collide with 'weekly'"):
        collections.define_collection(
            str(workspace), "ws", "u1", "yearly", "year", "one", "archive/{year}", "{key}"
        )

    # refusal proves nothing was written or committed, not just that it raised
    assert _head_sha(workspace) == sha_before
    assert (workspace / ".kajet" / "collections.yaml").read_text() == text_before
    assert "yearly" not in collections.list_collections(str(workspace))


def test_define_collection_redefining_itself_is_not_a_collision(collections, workspace):
    collections.define_collection(
        str(workspace), "ws", "u1", "weekly", "week", "one", "weekly/{year}", "{key}"
    )

    result = collections.define_collection(
        str(workspace), "ws", "u1", "weekly", "week", "one", "weekly/{year}", "{key}-v2"
    )

    assert result["verb"] == "update"


def test_delete_collection_removes_entry_without_touching_notes(collections, service, workspace):
    collections.define_collection(
        str(workspace), "ws", "u1", "weekly", "week", "one", "weekly/{year}", "{key}"
    )
    saved = service.save(
        "u1", "ws", str(workspace), "2026-W23", "content\n", [], folder="weekly/2026"
    )

    result = collections.delete_collection(str(workspace), "weekly")

    assert result == {"name": "weekly", "deleted": True}
    assert "weekly" not in collections.list_collections(str(workspace))
    still_there = service.get_with_content(saved["note_id"], "u1", str(workspace))
    assert still_there.folder == "weekly/2026"
    history = GitRepository(str(workspace)).file_history(".kajet/collections.yaml")
    assert history[0]["message"] == "collections: delete weekly"


def test_delete_collection_rejects_unknown_name(collections, workspace):
    with pytest.raises(ValueError, match="does not exist"):
        collections.delete_collection(str(workspace), "nope")
