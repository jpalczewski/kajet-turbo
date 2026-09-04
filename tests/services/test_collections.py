"""Write-verb coverage for CollectionService: add/redefine/delete policies."""

from datetime import date

import pytest

from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.services.collections import CollectionService


@pytest.fixture
def collections(service) -> CollectionService:
    return CollectionService(service._crud_repo, service)


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


# --- open_entry (#115) ---------------------------------------------------------


def test_open_entry_creates_missing_entry_with_occurred_at(collections, service, workspace):
    collections.define_collection(
        str(workspace), "ws", "u1", "journal", "day", "one", "journal/{year}/{month}", "{date}"
    )

    result = collections.open_entry(str(workspace), "ws", "u1", "journal", date(2026, 6, 15))

    assert result["created"] is True
    assert result["folder"] == "journal/2026/06"
    assert result["title"] == "2026-06-15"
    assert result["occurred_at"] == "2026-06-15"
    assert result["period"] is None
    note = service.get(result["note_id"], "u1")
    assert note["folder"] == "journal/2026/06"
    assert note["title"] == "2026-06-15"


def test_open_entry_resolves_existing_entry_instead_of_duplicating(collections, workspace):
    collections.define_collection(
        str(workspace), "ws", "u1", "journal", "day", "one", "journal/{year}/{month}", "{date}"
    )
    first = collections.open_entry(str(workspace), "ws", "u1", "journal", date(2026, 6, 15))

    second = collections.open_entry(str(workspace), "ws", "u1", "journal", date(2026, 6, 15))

    assert second["created"] is False
    assert second["note_id"] == first["note_id"]
    history = GitRepository(str(workspace)).file_history("journal/2026/06/2026-06-15.md")
    assert len(history) == 1  # only the first call ever wrote the file


def test_open_entry_week_grain_resolves_monday_and_sunday_to_one_note(collections, workspace):
    collections.define_collection(
        str(workspace), "ws", "u1", "weekly", "week", "one", "weekly/{year}", "{key}"
    )
    monday = date.fromisocalendar(2026, 23, 1)
    sunday = date.fromisocalendar(2026, 23, 7)

    from_monday = collections.open_entry(str(workspace), "ws", "u1", "weekly", monday)
    from_sunday = collections.open_entry(str(workspace), "ws", "u1", "weekly", sunday)

    assert from_sunday["created"] is False
    assert from_sunday["note_id"] == from_monday["note_id"]
    assert from_monday["period"] == "2026-W23"


def test_open_entry_many_cardinality_always_creates_and_allocates_next_ordinal(
    collections, service, workspace
):
    collections.define_collection(
        str(workspace),
        "ws",
        "u1",
        "sessions",
        "day",
        "many",
        "sessions/{year}/{month}",
        "{date} {ordinal}",
    )
    when = date(2026, 6, 15)

    first = collections.open_entry(str(workspace), "ws", "u1", "sessions", when)
    second = collections.open_entry(str(workspace), "ws", "u1", "sessions", when)

    assert (first["created"], first["ordinal"], first["title"]) == (
        True,
        1,
        "2026-06-15 1",
    )
    assert (second["created"], second["ordinal"], second["title"]) == (
        True,
        2,
        "2026-06-15 2",
    )
    assert second["note_id"] != first["note_id"]


def test_open_entry_many_cardinality_skips_gap_never_reuses_ordinal(
    collections, service, workspace
):
    collections.define_collection(
        str(workspace),
        "ws",
        "u1",
        "sessions",
        "day",
        "many",
        "sessions/{year}/{month}",
        "{date} {ordinal}",
    )
    when = date(2026, 6, 15)
    # A note that already occupies ordinal 3, created outside open_entry entirely —
    # _next_ordinal must still see it and skip past it, not just count its own writes.
    service.save("u1", "ws", str(workspace), "2026-06-15 3", "", [], folder="sessions/2026/06")

    result = collections.open_entry(str(workspace), "ws", "u1", "sessions", when)

    assert result["ordinal"] == 4
    assert result["title"] == "2026-06-15 4"


def test_open_entry_unknown_collection_raises(collections, workspace):
    with pytest.raises(ValueError, match="does not exist"):
        collections.open_entry(str(workspace), "ws", "u1", "nope", date(2026, 6, 15))
