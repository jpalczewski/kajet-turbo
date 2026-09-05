from pathlib import Path

import pytest
from sqlmodel import Session

from kajet_turbo.db import Database
from kajet_turbo.errors import TargetError
from kajet_turbo.models import Note
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.services.targets import (
    BatchTargetResolutionError,
    NoteTarget,
    TargetFailure,
    TargetResolutionError,
    TargetResolver,
    _DenialReason,
    _ValidationReason,
    is_denial,
)
from kajet_turbo.services.workspaces import WorkspaceService
from tests.services.conftest import build_workspace_service


@pytest.fixture
def workspace_service(database: Database) -> WorkspaceService:
    return build_workspace_service(database)


@pytest.fixture
def resolver(database: Database, workspace_service: WorkspaceService) -> TargetResolver:
    return TargetResolver(NoteRepository(database.engine), workspace_service)


def _user(database: Database, email: str = "a@b.com") -> str:
    return UserRepository(database.engine).create(email, "hash")


def _note(database: Database, *, note_id: str, workspace: str, owner_id: str) -> None:
    with Session(database.engine) as session:
        session.add(
            Note(
                id=note_id,
                workspace=workspace,
                owner_id=owner_id,
                title=note_id,
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
        )
        session.commit()


# --- workspace() ---


def test_workspace_resolves_when_access_granted(
    database: Database, workspace_service: WorkspaceService, resolver: TargetResolver
):
    uid = _user(database)
    workspace_service._repo.grant_access(uid, "ws")

    target = resolver.workspace(uid, "ws")

    assert target.owner_id == uid
    assert target.name == "ws"
    assert target.path == Path(workspace_service.workspace_path(uid, "ws"))


def test_workspace_denies_access_without_grant(database: Database, resolver: TargetResolver):
    uid = _user(database)

    with pytest.raises(TargetResolutionError) as exc_info:
        resolver.workspace(uid, "ws")

    failure = exc_info.value.failure
    assert failure.error == TargetError.ACCESS_DENIED
    assert is_denial(failure.reason)


def test_two_users_with_identically_named_workspaces_do_not_cross(
    database: Database, workspace_service: WorkspaceService, resolver: TargetResolver
):
    uid_a = _user(database, "a@b.com")
    uid_b = _user(database, "b@b.com")
    workspace_service._repo.grant_access(uid_a, "ws")
    workspace_service._repo.grant_access(uid_b, "ws")

    target_a = resolver.workspace(uid_a, "ws")
    target_b = resolver.workspace(uid_b, "ws")

    assert target_a.path != target_b.path
    assert target_a.path == Path(workspace_service.workspace_path(uid_a, "ws"))
    assert target_b.path == Path(workspace_service.workspace_path(uid_b, "ws"))


# --- note() ---


def test_note_resolves_when_owned_and_workspace_accessible(
    database: Database, workspace_service: WorkspaceService, resolver: TargetResolver
):
    uid = _user(database)
    workspace_service._repo.grant_access(uid, "ws")
    _note(database, note_id="n1", workspace="ws", owner_id=uid)

    target = resolver.note(uid, "n1")

    assert target.note_id == "n1"
    assert target.workspace.name == "ws"
    assert target.workspace.owner_id == uid


def test_note_not_found_when_row_missing(database: Database, resolver: TargetResolver):
    uid = _user(database)

    with pytest.raises(TargetResolutionError) as exc_info:
        resolver.note(uid, "does-not-exist")

    failure = exc_info.value.failure
    assert failure.error == TargetError.NOT_FOUND
    assert failure.reason == _DenialReason.MISSING_ROW


def test_note_not_found_when_owned_by_someone_else(database: Database, resolver: TargetResolver):
    owner = _user(database, "owner@b.com")
    other = _user(database, "other@b.com")
    _note(database, note_id="n1", workspace="ws", owner_id=owner)

    with pytest.raises(TargetResolutionError) as exc_info:
        resolver.note(other, "n1")

    failure = exc_info.value.failure
    # Same public shape as a genuinely missing id -- no owner disclosure through the
    # error, only the private reason distinguishes "not yours" from "does not exist".
    assert failure.error == TargetError.NOT_FOUND
    assert failure.reason == _DenialReason.WRONG_OWNER


def test_note_not_found_when_workspace_access_was_revoked(
    database: Database, resolver: TargetResolver
):
    """A note's owner_id can outlive a revoked WorkspaceAccess row -- the resolver must
    not trust the note row's implicit workspace association without an independent
    access check."""
    uid = _user(database)
    _note(database, note_id="n1", workspace="ws", owner_id=uid)  # no grant_access call

    with pytest.raises(TargetResolutionError) as exc_info:
        resolver.note(uid, "n1")

    failure = exc_info.value.failure
    assert failure.error == TargetError.NOT_FOUND
    assert failure.reason == _DenialReason.WORKSPACE_ACCESS_DENIED


def test_note_never_touches_the_filesystem(database: Database, resolver: TargetResolver):
    """A resolved workspace path need not exist on disk -- resolution is DB-only, which
    is what makes '404 before file access' a testable property at the adapter layer."""
    uid = _user(database)
    workspace_service = build_workspace_service(database)
    workspace_service._repo.grant_access(uid, "ws-never-created")
    _note(database, note_id="n1", workspace="ws-never-created", owner_id=uid)

    target = resolver.note(uid, "n1")

    assert not target.workspace.path.exists()


# --- notes() ---


def test_notes_batch_preserves_order_duplicates_and_partial_failure(
    database: Database, workspace_service: WorkspaceService, resolver: TargetResolver
):
    uid = _user(database)
    workspace_service._repo.grant_access(uid, "ws")
    _note(database, note_id="n1", workspace="ws", owner_id=uid)
    _note(database, note_id="n2", workspace="ws", owner_id=uid)

    results = resolver.notes(uid, ["n1", "missing", "n2", "n1"])

    assert len(results) == 4
    assert isinstance(results[0], NoteTarget)
    assert results[0].note_id == "n1"
    assert isinstance(results[1], TargetFailure)
    assert results[1].error == TargetError.NOT_FOUND
    assert results[1].index == 1
    assert isinstance(results[2], NoteTarget)
    assert results[2].note_id == "n2"
    assert isinstance(results[3], NoteTarget)
    assert results[3].note_id == "n1"  # duplicate id gets its own entry, not dropped


def test_notes_batch_missing_and_inaccessible_share_public_shape(
    database: Database, resolver: TargetResolver
):
    owner = _user(database, "owner@b.com")
    other = _user(database, "other@b.com")
    _note(database, note_id="n1", workspace="ws", owner_id=owner)

    results = resolver.notes(other, ["n1", "does-not-exist"])

    assert isinstance(results[0], TargetFailure)
    assert results[0].error == TargetError.NOT_FOUND
    assert results[0].reason == _DenialReason.WRONG_OWNER
    assert isinstance(results[1], TargetFailure)
    assert results[1].error == TargetError.NOT_FOUND
    assert results[1].reason == _DenialReason.MISSING_ROW


# --- notes_in_one_workspace() ---


def test_notes_in_one_workspace_rejects_empty_batch(database: Database, resolver: TargetResolver):
    uid = _user(database)
    with pytest.raises(BatchTargetResolutionError) as exc_info:
        resolver.notes_in_one_workspace(uid, [])
    assert exc_info.value.failures[0].reason == _ValidationReason.EMPTY_BATCH


def test_notes_in_one_workspace_rejects_oversized_batch(
    database: Database, resolver: TargetResolver
):
    uid = _user(database)
    with pytest.raises(BatchTargetResolutionError) as exc_info:
        resolver.notes_in_one_workspace(uid, [f"n{i}" for i in range(51)])
    assert exc_info.value.failures[0].reason == _ValidationReason.OVERSIZED_BATCH


def test_notes_in_one_workspace_rejects_malformed_and_duplicate_ids(
    database: Database, workspace_service: WorkspaceService, resolver: TargetResolver
):
    uid = _user(database)
    workspace_service._repo.grant_access(uid, "ws")
    _note(database, note_id="n1", workspace="ws", owner_id=uid)

    with pytest.raises(BatchTargetResolutionError) as exc_info:
        resolver.notes_in_one_workspace(uid, ["n1", "", "n1"])

    reasons = {f.index: f.reason for f in exc_info.value.failures}
    assert reasons[1] == _ValidationReason.MALFORMED_ID
    assert reasons[2] == _ValidationReason.DUPLICATE_ID
    assert 0 not in reasons  # the one valid, non-duplicate item is not itself a failure


def test_notes_in_one_workspace_rejects_missing_note(
    database: Database, workspace_service: WorkspaceService, resolver: TargetResolver
):
    uid = _user(database)
    workspace_service._repo.grant_access(uid, "ws")
    _note(database, note_id="n1", workspace="ws", owner_id=uid)

    with pytest.raises(BatchTargetResolutionError) as exc_info:
        resolver.notes_in_one_workspace(uid, ["n1", "missing"])

    (failure,) = exc_info.value.failures
    assert failure.index == 1
    assert failure.error == TargetError.NOT_FOUND


def test_notes_in_one_workspace_rejects_mixed_workspaces(
    database: Database, workspace_service: WorkspaceService, resolver: TargetResolver
):
    uid = _user(database)
    workspace_service._repo.grant_access(uid, "ws-a")
    workspace_service._repo.grant_access(uid, "ws-b")
    _note(database, note_id="n1", workspace="ws-a", owner_id=uid)
    _note(database, note_id="n2", workspace="ws-b", owner_id=uid)

    with pytest.raises(BatchTargetResolutionError) as exc_info:
        resolver.notes_in_one_workspace(uid, ["n1", "n2"])

    (failure,) = exc_info.value.failures
    assert failure.error == TargetError.MIXED_WORKSPACES
    assert failure.reason == _ValidationReason.MIXED_WORKSPACES


def test_notes_in_one_workspace_success_preserves_order(
    database: Database, workspace_service: WorkspaceService, resolver: TargetResolver
):
    uid = _user(database)
    workspace_service._repo.grant_access(uid, "ws")
    _note(database, note_id="n1", workspace="ws", owner_id=uid)
    _note(database, note_id="n2", workspace="ws", owner_id=uid)
    _note(database, note_id="n3", workspace="ws", owner_id=uid)

    workspace, targets = resolver.notes_in_one_workspace(uid, ["n3", "n1", "n2"])

    assert workspace.name == "ws"
    assert [t.note_id for t in targets] == ["n3", "n1", "n2"]
    assert all(t.workspace == workspace for t in targets)
