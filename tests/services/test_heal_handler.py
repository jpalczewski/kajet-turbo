from datetime import UTC, datetime
from pathlib import Path

import pytest

from kajet_turbo.db import Database
from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.services.heal_handler import HealDanglingHandler
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.notes import NoteService


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


def test_heal_orphan_source_unresolved_target_cleaned(note_repo, dangling, uid):
    # Source note gone AND target never resolves — orphan row must still be deleted.
    dangling.replace_for_source("ghost-src", "ws", uid, [("", "NeverExists")])
    HealDanglingHandler(note_repo, dangling)({"user_id": uid, "workspace": "ws"})
    assert dangling.exists(uid, "ws") is False  # orphan cleaned despite unresolved target


def test_heal_empty_workspace_noop(note_repo, dangling, uid):
    # No rows at all — must not raise.
    HealDanglingHandler(note_repo, dangling)({"user_id": uid, "workspace": "ws"})


# ---------------------------------------------------------------------------
# End-to-end integration: real NoteService (validation off, dangling wired)
# ---------------------------------------------------------------------------


@pytest.fixture
def git_ws(tmp_path: Path) -> Path:
    from kajet_turbo.repositories.git import GitRepository

    ws = tmp_path / "ws"
    ws.mkdir()
    GitRepository.init(str(ws))
    return ws


def test_end_to_end_dangling_then_target_created(database, note_repo, dangling, uid, git_ws):
    """Full chain: save Source with [[Target]] (dangling written) -> save Target ->
    run HealDanglingHandler directly (simulating the worker) -> assert edge + cleanup."""
    repo = NoteRepository(database.engine)
    indexer = NoteIndexer(
        repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda owner_id: None,
        build_embedder=lambda cfg: None,
    )
    svc = NoteService(
        repo,
        indexer=indexer,
        link_validation_enabled=lambda ws, owner: False,
        dangling_repo=dangling,
    )

    # Save Source with a dangling wikilink to Target (not yet created)
    svc.save(uid, "ws", str(git_ws), "Source", "[[Target]]", tags=[])
    assert dangling.exists(uid, "ws") is True

    # Now create Target — resolves the dangling link
    svc.save(uid, "ws", str(git_ws), "Target", "body", tags=[])

    # Run the handler directly (what the worker does)
    HealDanglingHandler(repo, dangling)({"user_id": uid, "workspace": "ws"})

    src_id = repo.resolve_paths("ws", uid, [("", "Source")])[("", "Source")]
    tgt_id = repo.resolve_paths("ws", uid, [("", "Target")])[("", "Target")]
    assert repo.backlinks(tgt_id) == [src_id]
    assert dangling.exists(uid, "ws") is False
