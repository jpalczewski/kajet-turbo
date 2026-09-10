import json
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

from fastmcp.dependencies import CallArgument, Depends
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token, get_http_request

from kajet_turbo import identity
from kajet_turbo.concurrency import run_sync
from kajet_turbo.errors import SecurityEvent, SecurityReason
from kajet_turbo.log import client_ip_fields as _client_ip_fields_from_request
from kajet_turbo.log import log_permission_denied, log_security_event
from kajet_turbo.repositories.events import EventRepository
from kajet_turbo.repositories.git import PostCommitHooks
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.services.targets import (
    BatchTargetResolutionError,
    NoteTarget,
    TargetFailure,
    TargetResolutionError,
    TargetResolver,
    WorkspaceTarget,
    audit_denied,
)
from kajet_turbo.services.workspaces import WorkspaceService


def client_ip_fields() -> dict[str, str]:
    """client_ip/user_agent off the live per-message HTTP request (#351).

    Not an ambient ContextVar bound once in LoggingMiddleware: fastmcp's own
    dispatcher rebinds get_http_request's context fresh for every inbound
    JSON-RPC message (fastmcp.server.low_level.bind_request_context), unlike the
    session-init-time capture #71 worked around for session_id/request_id — so
    this is safe to call even mid tool-dispatch on a persistent session.
    RuntimeError means no request context is bound (e.g. a unit test calling a
    resolver directly outside any request), not a bug -- degrade to empty. The
    actual field extraction is kajet_turbo.log.client_ip_fields — shared with every
    HTTP-boundary call site that already has a Request in hand.
    """
    try:
        request = get_http_request()
    except RuntimeError:
        return {}
    return _client_ip_fields_from_request(request)


@dataclass(frozen=True, slots=True)
class McpDependencies:
    workspace_service: WorkspaceService
    oauth_repo: OAuthRepository
    event_repo: EventRepository
    post_commit_hooks: PostCommitHooks
    target_resolver: TargetResolver
    user_repo: UserRepository


_current_dependencies: ContextVar[McpDependencies | None] = ContextVar(
    "kajet_mcp_dependencies", default=None
)


def build_mcp_context(
    workspace_service: WorkspaceService,
    oauth_repo: OAuthRepository,
    event_repo: EventRepository,
    post_commit_hooks: PostCommitHooks,
    target_resolver: TargetResolver,
    user_repo: UserRepository,
) -> McpDependencies:
    return McpDependencies(
        workspace_service,
        oauth_repo,
        event_repo,
        post_commit_hooks,
        target_resolver,
        user_repo,
    )


@contextmanager
def use_mcp_context(dependencies: McpDependencies):
    token: Token[McpDependencies | None] = _current_dependencies.set(dependencies)
    try:
        yield
    finally:
        _current_dependencies.reset(token)


def _deps() -> McpDependencies:
    dependencies = _current_dependencies.get()
    if dependencies is None:
        raise RuntimeError("MCP dependencies are not bound to this tool invocation")
    return dependencies


def current_mcp_dependencies() -> McpDependencies:
    return _deps()


def _resolve_user() -> str:
    """Sync identity resolver; run via run_sync at the MCP boundary."""
    token = get_access_token()
    if token is None:
        raise ToolError("Authentication required.")
    # Resolve from the token itself. Going through client_authorizations meant "the last
    # user who authorized this client", so a second user's consent re-pointed tokens that
    # were already issued — see identity.resolve_bearer_user_id.
    user_id = identity.resolve_bearer_user_id(_deps().oauth_repo, token.token)
    if user_id is None:
        log_security_event(
            SecurityEvent.AUTH_FAILURE,
            level="WARNING",
            user_id=None,
            auth_method="oauth_token",
            reason=SecurityReason.NO_OWNER.value,
            client_id=token.client_id,
            **client_ip_fields(),
        )
        raise ToolError("Authentication required.")
    return user_id


async def require_user_id() -> str:
    return await run_sync(_resolve_user)


async def user_timezone(user_id: str = Depends(require_user_id)) -> str:
    """The caller's stored IANA timezone (`users.timezone`), for tools that need to
    answer "what is today for this user" — see periods.today_in. Depends on
    require_user_id rather than re-resolving the access token: uncalled_for's
    per-call Depends cache is keyed by factory, so a tool that also depends on
    WORKSPACE_TARGET (itself chained off require_user_id) pays for the token
    resolution once, not twice.
    """
    user = await run_sync(_deps().user_repo.get, user_id)
    if user is None:
        raise RuntimeError(f"oauth token resolved to a user_id with no user row: {user_id!r}")
    return user.timezone


USER_TIMEZONE = Depends(user_timezone)


async def require_workspace_access(name: str, user_id: str) -> list[str]:
    """Legacy workspace-access check kept for the handful of tools whose contract is
    unchanged by #248 (settings, update_workspace) and for search_notes's explicit-name
    branch, which needs the "available" list in its error body -- resolve_workspace_target
    only names the one workspace that was denied. Still audits the denial like every
    TargetResolutionError path, just without going through the resolver/TargetFailure
    machinery this helper predates."""
    available = await run_sync(_deps().workspace_service.list_accessible, user_id)
    if name in available:
        return available
    log_permission_denied(
        action="workspace.read",
        resource="workspace",
        caller_id=user_id,
        reason=SecurityReason.WORKSPACE_ACCESS_DENIED,
        workspace=name,
    )
    msg = f"Workspace '{name}' does not exist or is not accessible."
    raise ToolError(json.dumps({"error": msg, "available": available}))


async def resolve_note_target(
    note_id: str = CallArgument(),
    user_id: str = Depends(require_user_id),
) -> NoteTarget:
    """Bind (this call's own note_id, the authenticated caller's user_id) to an
    authorized NoteTarget via the shared resolver (#246) — this is what fixes the
    ID/path mismatch bug: no caller-supplied workspace path ever reaches the service
    directly, only what the resolver itself derived for `note_id`. No session-state
    dependency at all (#248).

    Every raise below must be a ToolError: fastmcp flattens a plain exception raised
    during Depends() resolution into an opaque RuntimeError("Failed to resolve
    dependency ...") with the original message dropped from str() — see
    fastmcp/server/dependencies.py — and that RuntimeError, not the original, is what
    ToolDispatchMiddleware's SERVICE_ERRORS mapping would then see.
    """
    try:
        return await run_sync(current_mcp_dependencies().target_resolver.note, user_id, note_id)
    except TargetResolutionError as e:
        audit_denied(
            e.failure, action="note.read", resource="note", caller_id=user_id, note_id=note_id
        )
        raise ToolError(f"Note not found: note_id={note_id}") from e


NOTE_TARGET = Depends(resolve_note_target)


async def resolve_optional_note_target(
    note_id: str | None = CallArgument(),
    user_id: str = Depends(require_user_id),
) -> NoteTarget | None:
    """Like resolve_note_target, but for get_note's note_id-XOR-title addressing: when
    title is used instead, note_id is None and there is nothing to resolve yet."""
    if note_id is None:
        return None
    return await resolve_note_target(note_id, user_id)


OPTIONAL_NOTE_TARGET = Depends(resolve_optional_note_target)


async def resolve_workspace_target(
    workspace: str = CallArgument(),
    user_id: str = Depends(require_user_id),
) -> WorkspaceTarget:
    """Resolves a real, schema-visible "workspace" tool parameter directly through the
    resolver, keyed on the authenticated caller, with no session-state involvement at
    all (#248)."""
    try:
        return await run_sync(
            current_mcp_dependencies().target_resolver.workspace, user_id, workspace
        )
    except TargetResolutionError as e:
        audit_denied(
            e.failure,
            action="workspace.read",
            resource="workspace",
            caller_id=user_id,
            workspace=workspace,
        )
        raise ToolError(f"Workspace not accessible: {workspace}") from e


WORKSPACE_TARGET = Depends(resolve_workspace_target)


async def resolve_notes_in_one_workspace(
    user_id: str, note_ids: list[str]
) -> tuple[WorkspaceTarget, list[NoteTarget]]:
    """Batch-write prevalidation for edit_notes/delete_notes: every note_id must be
    well-formed, unique, owned+accessible, and resolve into the same workspace, or the
    whole batch is rejected before any write -- this is the target half of what each
    service method's own destructive-item validation used to also do inline.

    The public message names only the public TargetError per index, never the private
    denial reason (e.g. "not found", never "wrong owner" vs. "missing row")."""
    try:
        return await run_sync(
            current_mcp_dependencies().target_resolver.notes_in_one_workspace, user_id, note_ids
        )
    except BatchTargetResolutionError as e:
        any_denied = False
        for failure in e.failures:
            if audit_denied(
                failure,
                action="note.batch_write",
                resource="note",
                caller_id=user_id,
                note_id=note_ids[failure.index] if failure.index is not None else None,
            ):
                any_denied = True
        details = ", ".join(
            f"index {f.index}: {f.error.value}" if f.index is not None else f.error.value
            for f in e.failures
        )
        error = ToolError(f"Batch rejected before any write -- {details}")
        # Chain only when a denial was already audited above: ToolDispatchMiddleware
        # reads __cause__ to tell a deliberate ToolError from one fastmcp wrapped around
        # something else, and logs the cause's type when there is one. A pure validation
        # failure (mixed workspaces, malformed/duplicate ids, empty/oversized batch) is
        # never audited as a denial, so it must stay uncaused here — chaining it would
        # misreport a client mistake as an internal TargetResolutionError.
        if any_denied:
            raise error from e
        raise error from None


async def resolve_notes(user_id: str, note_ids: list[str]) -> list[NoteTarget | TargetFailure]:
    """Batch-read resolution for get_notes: one result per input, in input order,
    including repeated ids -- a per-item failure never drops its siblings. Audits each
    denial individually; never raises."""
    results = await run_sync(current_mcp_dependencies().target_resolver.notes, user_id, note_ids)
    for r in results:
        if isinstance(r, TargetFailure):
            audit_denied(
                r,
                action="note.batch_read",
                resource="note",
                caller_id=user_id,
                note_id=note_ids[r.index] if r.index is not None else None,
            )
    return results
