from datetime import UTC, datetime

import pytest

from kajet_turbo.db import Database
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.services.heal_handler import HealDanglingHandler


def _now() -> str:
    return datetime.now(UTC).isoformat()


@pytest.fixture
def uid(database: Database) -> str:
    return UserRepository(database.engine).create("heal@test.com", "hash")


@pytest.fixture
def note_repo(database: Database) -> NoteRepository:
    return NoteRepository(database.engine)


@pytest.fixture
def dangling(database: Database) -> DanglingLinkRepository:
    return DanglingLinkRepository(database.engine)


def test_heal_links_resolved_dangling(note_repo, dangling, uid):
    # Source B has a dangling link to "A"; then A is created.
    # Run handler → B→A edge appears, dangling row gone.
    note_repo.insert("b", "ws", uid, "B", [], _now(), _now(), "body")
    dangling.replace_for_source("b", "ws", uid, [("", "A")])
    note_repo.insert("a", "ws", uid, "A", [], _now(), _now(), "body")
    HealDanglingHandler(note_repo, dangling)({"user_id": uid, "workspace": "ws"})
    assert note_repo.backlinks("a") == ["b"]
    assert dangling.exists(uid, "ws") is False


def test_heal_idempotent(note_repo, dangling, uid):
    note_repo.insert("b", "ws", uid, "B", [], _now(), _now(), "body")
    dangling.replace_for_source("b", "ws", uid, [("", "A")])
    note_repo.insert("a", "ws", uid, "A", [], _now(), _now(), "body")
    handler = HealDanglingHandler(note_repo, dangling)
    handler({"user_id": uid, "workspace": "ws"})
    handler({"user_id": uid, "workspace": "ws"})  # second run is a clean no-op
    assert note_repo.backlinks("a") == ["b"]


def test_heal_leaves_unresolved(note_repo, dangling, uid):
    note_repo.insert("b", "ws", uid, "B", [], _now(), _now(), "body")
    dangling.replace_for_source("b", "ws", uid, [("", "A")])  # A never created
    HealDanglingHandler(note_repo, dangling)({"user_id": uid, "workspace": "ws"})
    assert dangling.exists(uid, "ws") is True  # still dangling


def test_heal_orphan_source_cleaned(note_repo, dangling, uid):
    # Dangling row whose source note no longer exists -> row deleted, no edge.
    dangling.replace_for_source("ghost-src", "ws", uid, [("", "A")])
    note_repo.insert("a", "ws", uid, "A", [], _now(), _now(), "body")
    HealDanglingHandler(note_repo, dangling)({"user_id": uid, "workspace": "ws"})
    assert dangling.exists(uid, "ws") is False  # orphan cleaned
    assert note_repo.backlinks("a") == []  # no edge created for a vanished source


def test_heal_empty_workspace_noop(note_repo, dangling, uid):
    # No rows at all — must not raise.
    HealDanglingHandler(note_repo, dangling)({"user_id": uid, "workspace": "ws"})
