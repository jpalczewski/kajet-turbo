"""Direct coverage for NoteReconcileService.clear_workspace_data (#225): constructed
without a NoteService, cleared atomically, scoped to its own domain and its own
workspace/owner."""

import pytest

from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.notes import NoteRepository
from tests.conftest import seed_user
from tests.services.conftest import build_note_reconcile_service
from tests.services.helpers import make_flaky_db_write, seed_full_workspace, workspace_table_counts

# Tables NoteTeardown.workspace_in_session actually touches — everything else
# workspace_table_counts reports (folder_meta, workspace_remote/access/meta, jobs,
# link_reconcile_dirty) is WorkspaceService.delete's job, not this service's.
_NOTE_DOMAIN_TABLES = {
    "notes",
    "note_tags",
    "tags",
    "note_links",
    "note_share_links",
    "dangling_links",
}


def _service(database):
    # A real DanglingLinkRepository, like production wiring — the default `None` in
    # build_note_reconcile_service makes dangling-link teardown a silent no-op, matching
    # NoteLinkService's own "no dangling repo configured" contract.
    return build_note_reconcile_service(
        database, dangling_repo=DanglingLinkRepository(database.engine)
    )


def test_clear_workspace_data_wipes_only_the_note_domain(database):
    seed_full_workspace(database, user_id="u1", name="ws")
    before = workspace_table_counts(database, workspace="ws", owner_id="u1")
    assert all(before[k] > 0 for k in _NOTE_DOMAIN_TABLES), before

    _service(database).clear_workspace_data("ws", "u1")

    after = workspace_table_counts(database, workspace="ws", owner_id="u1")
    for table in _NOTE_DOMAIN_TABLES:
        assert after[table] == 0, (table, after)
    # untouched: not this service's responsibility
    for table in set(after) - _NOTE_DOMAIN_TABLES:
        assert after[table] == before[table], (table, before, after)


def test_clear_workspace_data_is_owner_scoped(database):
    seed_full_workspace(database, user_id="u1", name="ws")
    seed_full_workspace(database, user_id="u2", name="ws")

    _service(database).clear_workspace_data("ws", "u1")

    other = workspace_table_counts(database, workspace="ws", owner_id="u2")
    assert all(other[k] > 0 for k in _NOTE_DOMAIN_TABLES), other


def test_clear_workspace_data_is_idempotent(database):
    seed_user(database, "u1")
    svc = _service(database)

    svc.clear_workspace_data("ws", "u1")
    svc.clear_workspace_data("ws", "u1")  # must not raise on an already-empty workspace


def test_clear_workspace_data_rolls_back_on_mid_cleanup_failure(
    database, monkeypatch: pytest.MonkeyPatch
):
    """tags and chunks are deleted before the notes row in NoteTeardown's fixed order
    (FK ordering) — failing on the notes delete must still leave the earlier deletes
    inside the same transaction, so nothing is left half-cleared."""
    seed_full_workspace(database, user_id="u1", name="ws")
    before = workspace_table_counts(database, workspace="ws", owner_id="u1")

    monkeypatch.setattr(
        NoteRepository,
        "delete_for_workspace_in_session",
        make_flaky_db_write(NoteRepository.delete_for_workspace_in_session, fail_on_call=1),
    )

    with pytest.raises(RuntimeError, match="db exploded"):
        _service(database).clear_workspace_data("ws", "u1")

    after = workspace_table_counts(database, workspace="ws", owner_id="u1")
    assert after == before
