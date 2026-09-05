import json
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import timedelta

from fastmcp.dependencies import CallArgument, CurrentContext, Depends
from fastmcp.exceptions import ToolError
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_access_token, get_context

from kajet_turbo import identity
from kajet_turbo.concurrency import run_sync
from kajet_turbo.log import log_permission_denied, logger
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.events import EventRepository
from kajet_turbo.repositories.git import PostCommitHooks
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.services.targets import (
    BatchTargetResolutionError,
    NoteTarget,
    TargetFailure,
    TargetResolutionError,
    TargetResolver,
    WorkspaceTarget,
    is_denial,
)
from kajet_turbo.services.workspaces import WorkspaceService

# Global per-user fallback scope (ActiveWorkspaceRepository's default "user" scope).
# Bridges the claude.ai connector's per-tool-call session churn (it never echoes back
# Mcp-Session-Id, so mcp-session-scoped state can't survive to the next call) at the cost
# of a bounded window where two concurrent conversations of the same user can clobber
# each other's active workspace. TTL keeps that window small.
USER_SCOPE = "user"
USER_SCOPE_TTL = timedelta(hours=1)


@dataclass(frozen=True)
class ActiveWorkspace:
    owner_id: str
    name: str
    path: str


@dataclass(frozen=True, slots=True)
class McpDependencies:
    workspace_service: WorkspaceService
    oauth_repo: OAuthRepository
    active_workspace_repo: ActiveWorkspaceRepository
    event_repo: EventRepository
    post_commit_hooks: PostCommitHooks
    target_resolver: TargetResolver


_current_dependencies: ContextVar[McpDependencies | None] = ContextVar(
    "kajet_mcp_dependencies", default=None
)
MCP_CONTEXT = CurrentContext()


def build_mcp_context(
    workspace_service: WorkspaceService,
    oauth_repo: OAuthRepository,
    active_workspace_repo: ActiveWorkspaceRepository,
    event_repo: EventRepository,
    post_commit_hooks: PostCommitHooks,
    target_resolver: TargetResolver,
) -> McpDependencies:
    return McpDependencies(
        workspace_service,
        oauth_repo,
        active_workspace_repo,
        event_repo,
        post_commit_hooks,
        target_resolver,
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
        raise ToolError("Wymagane zalogowanie.")
    # Resolve from the token itself. Going through client_authorizations meant "the last
    # user who authorized this client", so a second user's consent re-pointed tokens that
    # were already issued — see identity.resolve_bearer_user_id.
    user_id = identity.resolve_bearer_user_id(_deps().oauth_repo, token.token)
    if user_id is None:
        logger.warning("mcp_token_without_user", client_id=token.client_id)
        raise ToolError("Wymagane zalogowanie.")
    return user_id


async def require_user_id() -> str:
    return await run_sync(_resolve_user)


async def require_workspace_access(name: str, user_id: str) -> list[str]:
    available = await run_sync(_deps().workspace_service.list_accessible, user_id)
    if name in available:
        return available
    msg = f"Workspace '{name}' nie istnieje lub brak dostępu."
    raise ToolError(json.dumps({"error": msg, "available": available}))


async def _clear_active_workspace_state(ctx: Context) -> None:
    await ctx.delete_state("active_workspace")
    await ctx.delete_state("active_user_id")


async def _validate_active_workspace(ctx: Context, name: str, user_id: str) -> None:
    try:
        await require_workspace_access(name, user_id)
    except ToolError:
        await _clear_active_workspace_state(ctx)
        logger.warning("active_workspace_access_revoked", user_id=user_id, ws=name)
        raise


async def _rehydrate_from_db(
    ctx: Context, user_id: str, db_name: str, *, source: str, scope: str
) -> ActiveWorkspace:
    await _validate_active_workspace(ctx, db_name, user_id)
    await ctx.set_state("active_workspace", db_name)
    await ctx.set_state("active_user_id", user_id)
    logger.info("active_workspace_resolved", source=source, ws=db_name, scope=scope)
    return ActiveWorkspace(
        owner_id=user_id,
        name=db_name,
        path=_deps().workspace_service.workspace_path(user_id, db_name),
    )


async def active_workspace(ctx: Context = MCP_CONTEXT) -> ActiveWorkspace:
    """Resolve active workspace: session state, then the session-scoped DB row, then a
    time-boxed per-user DB row (see USER_SCOPE docstring above).

    Every raise below must be a ToolError (or another FastMCPError), never a plain
    exception: fastmcp resolves Depends() params in _resolve_fastmcp_dependencies
    *before* the wrapped tool coroutine runs, so logged_tool's SERVICE_ERRORS mapping
    never sees a failure here. A plain exception would instead be flattened into an
    opaque RuntimeError("Failed to resolve dependency ...") with the original message
    dropped from str() — see fastmcp/server/dependencies.py.
    """
    user_id = await require_user_id()
    name = await ctx.get_state("active_workspace")
    if name:
        stored_user_id = await ctx.get_state("active_user_id")
        if stored_user_id != user_id:
            await _clear_active_workspace_state(ctx)
            logger.warning(
                "active_workspace_identity_mismatch",
                stored_user_id=stored_user_id,
                current_user_id=user_id,
                ws=name,
            )
            raise ToolError(
                "MCP session identity changed. Reconnect and activate a workspace again."
            )
        await _validate_active_workspace(ctx, name, user_id)
        logger.debug("active_workspace_resolved", source="session", ws=name)
        return ActiveWorkspace(
            owner_id=user_id,
            name=name,
            path=_deps().workspace_service.workspace_path(user_id, name),
        )
    scope = active_workspace_scope(ctx)
    if scope is not None:
        db_name = await run_sync(_deps().active_workspace_repo.get, user_id, scope)
        if db_name:
            return await _rehydrate_from_db(
                ctx, user_id, db_name, source="session_db_fallback", scope=scope
            )

    db_name = await run_sync(_deps().active_workspace_repo.get, user_id, USER_SCOPE, USER_SCOPE_TTL)
    if db_name:
        return await _rehydrate_from_db(
            ctx, user_id, db_name, source="user_scope_fallback", scope=USER_SCOPE
        )

    logger.info("active_workspace_miss")
    raise ToolError("Wywołaj activate_workspace() najpierw.")


def active_workspace_scope(ctx: Context) -> str | None:
    session_id = _context_session_id(ctx)
    if session_id:
        return f"mcp-session:{session_id}"
    return None


def _context_session_id(ctx: Context) -> str | None:
    try:
        return ctx.session_id
    except Exception:
        pass
    try:
        return get_context().session_id
    except Exception:
        return None


ACTIVE_WORKSPACE = Depends(active_workspace)


async def resolve_note_target(
    note_id: str = CallArgument(),
    ws: ActiveWorkspace = CallArgument(),
) -> NoteTarget:
    """Bind (this call's own note_id, the already-resolved active workspace's owner_id)
    to an authorized NoteTarget via the shared resolver (#246) — this is what fixes the
    ID/path mismatch bug: `ws.path` never reaches the service directly, only what the
    resolver itself derived for `note_id`.

    Per #246+#248 sequencing: `ws` still comes from ACTIVE_WORKSPACE (external schema
    unchanged in this phase) — CallArgument reads its already-resolved value, not a
    literal caller-supplied argument. #248 replaces this with a plain
    Depends(require_user_id), once activate_workspace/ACTIVE_WORKSPACE are gone.

    Every raise below must be a ToolError, matching active_workspace()'s own docstring
    warning: a plain exception here is flattened into an opaque RuntimeError by fastmcp's
    dependency resolution, before logged_tool's error mapping ever sees it.
    """
    try:
        return await run_sync(current_mcp_dependencies().target_resolver.note, ws.owner_id, note_id)
    except TargetResolutionError as e:
        if is_denial(e.failure.reason):
            log_permission_denied(
                action="note.read",
                resource="note",
                caller_id=ws.owner_id,
                reason=e.failure.reason,
                note_id=note_id,
            )
        raise ToolError(f"Note not found: note_id={note_id}") from e


NOTE_TARGET = Depends(resolve_note_target)


async def resolve_optional_note_target(
    note_id: str | None = CallArgument(),
    ws: ActiveWorkspace = CallArgument(),
) -> NoteTarget | None:
    """Like resolve_note_target, but for get_note's note_id-XOR-title addressing: when
    title is used instead, note_id is None and there is nothing to resolve yet."""
    if note_id is None:
        return None
    return await resolve_note_target(note_id, ws)


OPTIONAL_NOTE_TARGET = Depends(resolve_optional_note_target)


async def reauthorize_workspace(ws: ActiveWorkspace) -> WorkspaceTarget:
    """Workspace-scoped tools (save_note, list_notes, export_folder, grep, ...) must not
    trust `ActiveWorkspace.path` directly -- it is legacy active-workspace state, not a
    resolver output. Re-derive a WorkspaceTarget from the resolver before calling the
    (already-migrated) service entry point, same "reauthorize the legacy selection"
    requirement #246 places on note-addressed tools."""
    try:
        return await run_sync(
            current_mcp_dependencies().target_resolver.workspace, ws.owner_id, ws.name
        )
    except TargetResolutionError as e:
        if is_denial(e.failure.reason):
            log_permission_denied(
                action="workspace.read",
                resource="workspace",
                caller_id=ws.owner_id,
                reason=e.failure.reason,
                workspace=ws.name,
            )
        raise ToolError(f"Workspace not accessible: {ws.name}") from e


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
        for failure in e.failures:
            if is_denial(failure.reason):
                log_permission_denied(
                    action="note.batch_write",
                    resource="note",
                    caller_id=user_id,
                    reason=failure.reason,
                    note_id=note_ids[failure.index] if failure.index is not None else None,
                )
        details = ", ".join(
            f"index {f.index}: {f.error.value}" if f.index is not None else f.error.value
            for f in e.failures
        )
        raise ToolError(f"Batch rejected before any write -- {details}") from e


async def resolve_notes(user_id: str, note_ids: list[str]) -> list[NoteTarget | TargetFailure]:
    """Batch-read resolution for get_notes: one result per input, in input order,
    including repeated ids -- a per-item failure never drops its siblings. Audits each
    denial individually; never raises."""
    results = await run_sync(current_mcp_dependencies().target_resolver.notes, user_id, note_ids)
    for r in results:
        if isinstance(r, TargetFailure) and is_denial(r.reason):
            log_permission_denied(
                action="note.batch_read",
                resource="note",
                caller_id=user_id,
                reason=r.reason,
                note_id=note_ids[r.index] if r.index is not None else None,
            )
    return results
