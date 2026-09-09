from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, TypeGuard

from kajet_turbo.errors import SecurityReason, TargetError
from kajet_turbo.log import log_permission_denied
from kajet_turbo.repositories.notes import NoteRepository

if TYPE_CHECKING:
    # Deferred: services.workspaces imports services.notes (NoteCreateService et al.),
    # which imports this module for the NoteTarget/WorkspaceTarget entry-point signatures -- a
    # module-level import here would cycle back through that partially-initialized
    # package. Type-only; TargetResolver never constructs a WorkspaceService itself.
    from kajet_turbo.services.workspaces import WorkspaceService

_MAX_BATCH = 50


@dataclass(frozen=True, slots=True)
class WorkspaceTarget:
    # owner_id == the resolving user today because WorkspaceAccess (models.py) has no
    # sharing implementation yet -- has_access()/list_user_workspaces() only check row
    # existence for that user_id, even though the table already carries a `role` column.
    # When #203 adds real sharing, this must derive from the workspace row's actual
    # owner, not the caller -- do not let that assumption leak past this dataclass.
    owner_id: str
    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class NoteTarget:
    note_id: str
    workspace: WorkspaceTarget


class _ValidationReason(StrEnum):
    """Internal-only -- batch prevalidation failures that are NOT denials (no audit)."""

    EMPTY_BATCH = "empty_batch"
    OVERSIZED_BATCH = "oversized_batch"
    MALFORMED_ID = "malformed_id"
    DUPLICATE_ID = "duplicate_id"
    MIXED_WORKSPACES = "mixed_workspaces"


type DenialOrValidationReason = SecurityReason | _ValidationReason

_DENIAL_REASONS = frozenset(
    {
        SecurityReason.MISSING_ROW,
        SecurityReason.WRONG_OWNER,
        SecurityReason.WORKSPACE_ACCESS_DENIED,
        SecurityReason.WORKSPACE_MISMATCH,
    }
)


@dataclass(frozen=True, slots=True)
class TargetFailure:
    index: int | None
    error: TargetError
    # Private diagnostic -- callers use is_denial(reason) to
    # decide whether to emit a permission_denied audit record; never surface this
    # value itself in an HTTP body or ToolError message.
    reason: DenialOrValidationReason


class TargetResolutionError(Exception):
    """Single-target resolution failure (workspace() / note())."""

    def __init__(self, failure: TargetFailure) -> None:
        self.failure = failure
        # Public error code only -- failure.reason is the private diagnostic this
        # dataclass exists to keep out of default serialization.
        super().__init__(f"target resolution failed: {failure.error}")


class BatchTargetResolutionError(Exception):
    """notes_in_one_workspace() prevalidation failure -- carries every offending item,
    not just the first, so a caller can report a full list of bad indices at once."""

    def __init__(self, failures: list[TargetFailure]) -> None:
        self.failures = failures
        super().__init__(f"{len(failures)} target(s) failed prevalidation")


def is_denial(reason: DenialOrValidationReason) -> TypeGuard[SecurityReason]:
    """True when `reason` should be audited as a permission_denied event."""
    return reason in _DENIAL_REASONS


def audit_denied(
    failure: TargetFailure, *, action: str, resource: str, caller_id: str, **extra
) -> bool:
    """Shared denial-audit skeleton for TargetResolutionError/TargetFailure handlers
    (#248): does the is_denial check plus the log_permission_denied call, and reports
    whether this failure was a denial, so a caller can still decide its own error
    chaining. Never raises -- callers raise their own ToolError/HTTPException with
    their own status and message; this only centralizes the repeated audit-logging
    skeleton duplicated across both adapters."""
    reason = failure.reason
    if is_denial(reason):
        log_permission_denied(
            action=action, resource=resource, caller_id=caller_id, reason=reason, **extra
        )
        return True
    return False


class TargetResolver:
    """The only place that binds (authenticated user_id, note_id/workspace name) to an
    authorized filesystem target. Pure domain logic -- no FastMCP/FastAPI import, no
    logging (that is the adapter's job, so it can decide what to audit)."""

    def __init__(self, note_repo: NoteRepository, workspace_service: WorkspaceService) -> None:
        self._note_repo = note_repo
        self._workspace_service = workspace_service

    def workspace(self, user_id: str, name: str) -> WorkspaceTarget:
        if not self._workspace_service.has_access(user_id, name):
            raise TargetResolutionError(
                TargetFailure(
                    None, TargetError.ACCESS_DENIED, SecurityReason.WORKSPACE_ACCESS_DENIED
                )
            )
        path = Path(self._workspace_service.workspace_path(user_id, name))
        return WorkspaceTarget(owner_id=user_id, name=name, path=path)

    def note(self, user_id: str, note_id: str) -> NoteTarget:
        note = self._note_repo.get(note_id)
        if note is None:
            raise TargetResolutionError(
                TargetFailure(None, TargetError.NOT_FOUND, SecurityReason.MISSING_ROW)
            )
        if note.owner_id != user_id:
            raise TargetResolutionError(
                TargetFailure(None, TargetError.NOT_FOUND, SecurityReason.WRONG_OWNER)
            )
        try:
            workspace = self.workspace(user_id, note.workspace)
        except TargetResolutionError as e:
            # A note's owner_id could theoretically outlive a revoked WorkspaceAccess
            # row -- defense in depth, not trusting the note row's implicit workspace
            # association without an independent access check. Public shape stays
            # NOT_FOUND either way; only the private reason differs.
            raise TargetResolutionError(
                TargetFailure(None, TargetError.NOT_FOUND, SecurityReason.WORKSPACE_ACCESS_DENIED)
            ) from e
        return NoteTarget(note_id=note_id, workspace=workspace)

    def notes(self, user_id: str, note_ids: Sequence[str]) -> list[NoteTarget | TargetFailure]:
        """One result per input, in input order, including repeated ids. A missing or
        inaccessible item becomes a per-item TargetFailure and never drops its
        siblings."""
        owned = {n.id: n for n in self._note_repo.get_many(list(note_ids), user_id)}
        workspace_cache: dict[str, WorkspaceTarget | TargetFailure] = {}
        results: list[NoteTarget | TargetFailure] = []
        for index, note_id in enumerate(note_ids):
            note = owned.get(note_id)
            if note is None:
                reason = self._missing_reason(note_id, user_id)
                results.append(TargetFailure(index, TargetError.NOT_FOUND, reason))
                continue
            cached = workspace_cache.get(note.workspace)
            if cached is None:
                try:
                    cached = self.workspace(user_id, note.workspace)
                except TargetResolutionError:
                    cached = TargetFailure(
                        index, TargetError.NOT_FOUND, SecurityReason.WORKSPACE_ACCESS_DENIED
                    )
                workspace_cache[note.workspace] = cached
            if isinstance(cached, TargetFailure):
                results.append(TargetFailure(index, cached.error, cached.reason))
            else:
                results.append(NoteTarget(note_id=note_id, workspace=cached))
        return results

    def notes_in_one_workspace(
        self, user_id: str, note_ids: Sequence[str]
    ) -> tuple[WorkspaceTarget, list[NoteTarget]]:
        """Prevalidate a write batch: raises BatchTargetResolutionError with every
        offending index before anything is mutated. Success preserves input order."""
        failures: list[TargetFailure] = []
        if not note_ids:
            raise BatchTargetResolutionError(
                [TargetFailure(None, TargetError.INVALID_INPUT, _ValidationReason.EMPTY_BATCH)]
            )
        if len(note_ids) > _MAX_BATCH:
            raise BatchTargetResolutionError(
                [TargetFailure(None, TargetError.INVALID_INPUT, _ValidationReason.OVERSIZED_BATCH)]
            )
        seen: set[str] = set()
        for index, note_id in enumerate(note_ids):
            if not note_id or not note_id.strip():
                failures.append(
                    TargetFailure(index, TargetError.INVALID_INPUT, _ValidationReason.MALFORMED_ID)
                )
            elif note_id in seen:
                failures.append(
                    TargetFailure(index, TargetError.INVALID_INPUT, _ValidationReason.DUPLICATE_ID)
                )
            else:
                seen.add(note_id)
        if failures:
            raise BatchTargetResolutionError(failures)

        owned = {n.id: n for n in self._note_repo.get_many(list(note_ids), user_id)}
        workspaces_seen: set[str] = set()
        for index, note_id in enumerate(note_ids):
            note = owned.get(note_id)
            if note is None:
                reason = self._missing_reason(note_id, user_id)
                failures.append(TargetFailure(index, TargetError.NOT_FOUND, reason))
                continue
            workspaces_seen.add(note.workspace)
        if failures:
            raise BatchTargetResolutionError(failures)

        if len(workspaces_seen) > 1:
            raise BatchTargetResolutionError(
                [
                    TargetFailure(
                        None, TargetError.MIXED_WORKSPACES, _ValidationReason.MIXED_WORKSPACES
                    )
                ]
            )
        (workspace_name,) = workspaces_seen
        try:
            workspace = self.workspace(user_id, workspace_name)
        except TargetResolutionError as e:
            raise BatchTargetResolutionError(
                [TargetFailure(None, TargetError.NOT_FOUND, SecurityReason.WORKSPACE_ACCESS_DENIED)]
            ) from e
        resolved = [NoteTarget(note_id=note_id, workspace=workspace) for note_id in note_ids]
        return workspace, resolved

    def _missing_reason(self, note_id: str, user_id: str) -> SecurityReason:
        """Distinguish why `note_id` is absent from a `get_many(..., user_id)` result.

        That query already filters on `Note.owner_id == user_id`, so absence has
        exactly two causes: no such row, or a row owned by someone else. There is no
        third "row is owned but its workspace access was revoked" case to detect here
        -- this never calls workspace access checks -- so don't invent one."""
        note = self._note_repo.get(note_id)
        if note is None:
            return SecurityReason.MISSING_ROW
        return SecurityReason.WRONG_OWNER
