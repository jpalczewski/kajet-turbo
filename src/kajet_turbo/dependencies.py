"""Application-owned collaborators and FastAPI dependency providers.

Importing this module is deliberately inert. A process may construct several apps
with different databases, workspace roots and credentials; the only place that
creates their collaborators is :func:`build_resources`.
"""

from __future__ import annotations

import asyncio
import functools
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from fastapi import Depends, HTTPException
from starlette.requests import HTTPConnection, Request

from kajet_turbo import identity
from kajet_turbo.auth import KajetOAuthProvider, create_auth
from kajet_turbo.crypto import cipher_for
from kajet_turbo.db import Database
from kajet_turbo.embedding import build_embedder, pooled_embedder_factory
from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository, QueryEmbeddingCache
from kajet_turbo.embedding.client import SharedEmbedderClient
from kajet_turbo.embedding.resolver import ProfileResolver
from kajet_turbo.errors import AuthError, NoteError, SecurityEvent, SecurityReason
from kajet_turbo.log import client_ip_fields, log_permission_denied, log_security_event
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.embedding_profiles import EmbeddingProfileRepository
from kajet_turbo.repositories.events import EventRepository
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.repositories.git import PostCommitHooks
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository
from kajet_turbo.repositories.notes import (
    NoteChunkRepository,
    NoteLinkRepository,
    NoteRepository,
    NoteTagRepository,
)
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.sessions import SessionRepository
from kajet_turbo.repositories.ssh_keys import SshKeyRepository
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.repositories.workspace_meta import WorkspaceMetaRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.services.collections import CollectionService
from kajet_turbo.services.embed_enqueue import make_enqueue_embed
from kajet_turbo.services.embed_handler import EmbedNoteHandler
from kajet_turbo.services.embedding_profiles import EmbeddingProfileService
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.jobs import JobService
from kajet_turbo.services.notes import (
    NoteCreateService,
    NoteDeleteService,
    NoteEditService,
    NoteFolderService,
    NoteLinkService,
    NoteReadService,
    NoteReconcileService,
    NoteSearchService,
    NoteShareLinkService,
    NoteTagService,
    NoteTemporalService,
    NoteVersionService,
)
from kajet_turbo.services.notes.persistence import NoteTeardown
from kajet_turbo.services.push_enqueue import make_enqueue_push_on_commit
from kajet_turbo.services.push_handler import PushHandler
from kajet_turbo.services.reconcile_links_handler import ReconcileLinksHandler
from kajet_turbo.services.reindex_handler import ReindexNoteHandler
from kajet_turbo.services.ssh_keys import SshKeyService
from kajet_turbo.services.targets import (
    NoteTarget,
    TargetResolutionError,
    TargetResolver,
    WorkspaceTarget,
    audit_denied,
)
from kajet_turbo.services.workspace_remote import WorkspaceRemoteService
from kajet_turbo.services.workspaces import WorkspaceService

if TYPE_CHECKING:
    from kajet_turbo.services.preferences import PreferencesService


@dataclass(frozen=True, slots=True)
class AppConfig:
    db_path: str = "/data/kajet.db"
    workspaces_dir: str = "/workspaces"
    mcp_base_url: str | None = None
    secret_key: str | None = None
    known_hosts_path: str = "/data/ssh/known_hosts"
    key_tmpdir: str = "/dev/shm"
    admin_email: str | None = None
    admin_password: str | None = None
    worker_poll_interval: float = 1.0
    worker_concurrency: int = 4
    worker_stale_after: float = 300.0
    serve_spa: bool = True

    @classmethod
    def from_env(cls) -> AppConfig:
        return cls(
            db_path=os.getenv("DB_PATH", "/data/kajet.db"),
            workspaces_dir=os.getenv("WORKSPACES_DIR", "/workspaces"),
            mcp_base_url=os.getenv("MCP_BASE_URL")
            or os.getenv("COOLIFY_FQDN")
            or os.getenv("COOLIFY_URL"),
            secret_key=os.getenv("SECRET_KEY"),
            known_hosts_path=os.getenv("KAJET_KNOWN_HOSTS", "/data/ssh/known_hosts"),
            key_tmpdir=os.getenv("KAJET_KEY_TMPDIR", "/dev/shm"),
            admin_email=os.getenv("KAJET_ADMIN_EMAIL"),
            admin_password=os.getenv("KAJET_ADMIN_PASSWORD"),
            worker_poll_interval=float(os.getenv("KAJET_WORKER_POLL_INTERVAL", "1")),
            worker_concurrency=int(os.getenv("KAJET_WORKER_CONCURRENCY", "4")),
            worker_stale_after=float(os.getenv("KAJET_WORKER_STALE_AFTER", "300")),
            serve_spa=os.getenv("KAJET_SERVE_SPA", "1") == "1",
        )


@dataclass(slots=True)
class AppResources:
    config: AppConfig
    db: Database
    note_repo: NoteRepository
    note_link_repo: NoteLinkRepository
    note_tag_repo: NoteTagRepository
    note_chunk_repo: NoteChunkRepository
    user_repo: UserRepository
    session_repo: SessionRepository
    workspace_repo: WorkspaceRepository
    oauth_repo: OAuthRepository
    provider: KajetOAuthProvider
    folder_meta_repo: FolderMetaRepository
    note_share_link_repo: NoteShareLinkRepository
    job_repo: JobRepository
    note_create_service: NoteCreateService
    note_edit_service: NoteEditService
    note_delete_service: NoteDeleteService
    note_tag_service: NoteTagService
    note_link_service: NoteLinkService
    note_folder_service: NoteFolderService
    note_temporal_service: NoteTemporalService
    note_version_service: NoteVersionService
    note_read_service: NoteReadService
    note_reconcile_service: NoteReconcileService
    note_search_service: NoteSearchService
    workspace_service: WorkspaceService
    target_resolver: TargetResolver
    collection_service: CollectionService
    embedding_profile_service: EmbeddingProfileService
    ssh_key_service: SshKeyService
    note_share_link_service: NoteShareLinkService
    preferences_service: PreferencesService
    workspace_remote_service: WorkspaceRemoteService
    job_service: JobService
    event_repo: EventRepository
    shared_embed_client: SharedEmbedderClient
    embed_handler: EmbedNoteHandler
    reindex_handler: ReindexNoteHandler
    reconcile_links_handler: ReconcileLinksHandler
    push_handler: PushHandler
    post_commit_hooks: PostCommitHooks
    _closed: bool = False

    async def aclose(self) -> None:
        """Release every closable resource once; DB cleanup still runs after client failure.

        A `finally` (not a second try/except) so a `db.close()` failure after the embed
        client also failed isn't silently dropped — it chains onto the first via
        `__context__` instead of disappearing.
        """
        if self._closed:
            return
        self._closed = True
        try:
            await self.shared_embed_client.aclose()
        finally:
            self.db.close()


def _probe_dim(base_url: str, model: str, api_key: str | None) -> int:
    cfg = EmbedderConfig(
        backend_id=base_url, type="openai", model=model, dim=0, base_url=base_url, api_key=api_key
    )

    async def run() -> int:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return len(await build_embedder(cfg, client).embed_query("probe"))

    return asyncio.run(run())


def build_resources(config: AppConfig) -> AppResources:
    """Synchronously construct one graph; the caller owns it until ASGI assembly succeeds."""
    from kajet_turbo.services.preferences import PreferencesService

    db = Database(config.db_path)
    try:
        note_repo = NoteRepository(db.engine)
        note_link_repo = NoteLinkRepository(db.engine)
        note_tag_repo = NoteTagRepository(db.engine)
        note_chunk_repo = NoteChunkRepository(db.engine)
        note_share_link_repo = NoteShareLinkRepository(db.engine)
        user_repo = UserRepository(db.engine)
        session_repo = SessionRepository(db.engine)
        workspace_repo = WorkspaceRepository(db.engine)
        oauth_repo = OAuthRepository(db.engine)
        provider = create_auth(oauth_repo, base_url=config.mcp_base_url)
        profile_repo = EmbeddingProfileRepository(db.engine)

        @functools.cache
        def profile_cipher():
            return cipher_for("embedding", config.secret_key)

        profile_resolver = ProfileResolver(profile_repo, profile_cipher)
        embedding_profile_service = EmbeddingProfileService(
            profile_repo, profile_cipher, _probe_dim
        )
        job_repo = JobRepository(db.engine)
        indexer = NoteIndexer(
            note_chunk_repo,
            EmbeddingCacheRepository(db.engine),
            profile_resolver.resolve_backend,
            job_repo,
            enqueue_embed=make_enqueue_embed(job_repo),
        )
        embed_handler = EmbedNoteHandler(
            note_chunk_repo,
            EmbeddingCacheRepository(db.engine),
            profile_resolver.resolve_backend,
            pooled_embedder_factory(),
        )
        reindex_handler = ReindexNoteHandler(
            note_repo,
            note_chunk_repo,
            job_repo,
            profile_resolver.resolve_backend,
            config.workspaces_dir,
        )
        workspace_meta_repo = WorkspaceMetaRepository(db.engine)
        dangling_repo = DanglingLinkRepository(db.engine)
        reconcile_repo = LinkReconcileRepository(db.engine, job_repo)
        tag_service = NoteTagService(note_repo, note_tag_repo, indexer)
        workspace_service: WorkspaceService
        link_service = NoteLinkService(
            note_repo,
            note_link_repo,
            note_tag_repo,
            dangling_repo,
            lambda ws, owner: workspace_service.get_settings(owner, ws)["validate_links"],
            job_repo,
        )
        note_teardown = NoteTeardown(
            note_tag_repo,
            note_chunk_repo,
            note_repo,
            note_link_repo,
            link_service,
            note_share_link_repo,
        )
        note_reconcile_service = NoteReconcileService(
            note_repo,
            note_tag_repo,
            link_service,
            note_teardown,
            indexer=indexer,
            reconcile_repo=reconcile_repo,
        )
        shared_embed_client = SharedEmbedderClient()
        note_search_service = NoteSearchService(
            note_chunk_repo,
            profile_resolver.resolve_backend,
            pooled_embedder_factory(),
            QueryEmbeddingCache(),
            note_repo,
            note_tag_repo,
            async_build_embedder=lambda cfg: build_embedder(cfg, shared_embed_client.get()),
        )
        folder_meta_repo = FolderMetaRepository(db.engine)
        note_share_link_repo = NoteShareLinkRepository(db.engine)
        folder_service = NoteFolderService(
            note_repo, link_service, folder_meta_repo, reconcile_repo
        )
        note_version_service = NoteVersionService(note_repo)
        note_create_service = NoteCreateService(
            note_repo,
            link_service,
            tag_service,
            indexer=indexer,
            reconcile_repo=reconcile_repo,
        )
        note_edit_service = NoteEditService(
            note_repo,
            link_service,
            tag_service,
            note_version_service,
            indexer=indexer,
            reconcile_repo=reconcile_repo,
        )
        note_delete_service = NoteDeleteService(
            note_repo,
            note_tag_repo,
            link_service,
            note_teardown,
            reconcile_repo=reconcile_repo,
        )
        note_temporal_service = NoteTemporalService(note_repo)
        note_read_service = NoteReadService(note_repo, note_tag_repo, link_service, indexer=indexer)
        ssh_key_repo = SshKeyRepository(db.engine)
        ssh_key_service = SshKeyService(
            ssh_key_repo, lambda: cipher_for("ssh-key", config.secret_key)
        )
        note_share_link_service = NoteShareLinkService(note_share_link_repo)
        workspace_remote_repo = WorkspaceRemoteRepository(db.engine)
        workspace_service = WorkspaceService(
            workspace_repo,
            note_repo,
            workspace_meta_repo,
            note_reconcile_service,
            dangling_repo,
            folder_meta_repo,
            workspace_remote_repo,
            job_repo,
            reconcile_repo=reconcile_repo,
            workspaces_dir=config.workspaces_dir,
        )
        push_handler = PushHandler(
            workspace_remote_repo,
            ssh_key_repo,
            lambda: cipher_for("ssh-key", config.secret_key),
            known_hosts_path=config.known_hosts_path,
            key_dir=config.key_tmpdir,
        )
        post_commit_hooks = PostCommitHooks()
        post_commit_hooks.register(
            make_enqueue_push_on_commit(job_repo, workspace_remote_repo, config.workspaces_dir)
        )
        reconcile_links_handler = ReconcileLinksHandler(
            note_repo, link_service, dangling_repo, reconcile_repo, config.workspaces_dir
        )
        return AppResources(
            config,
            db,
            note_repo,
            note_link_repo,
            note_tag_repo,
            note_chunk_repo,
            user_repo,
            session_repo,
            workspace_repo,
            oauth_repo,
            provider,
            folder_meta_repo,
            note_share_link_repo,
            job_repo,
            note_create_service,
            note_edit_service,
            note_delete_service,
            tag_service,
            link_service,
            folder_service,
            note_temporal_service,
            note_version_service,
            note_read_service,
            note_reconcile_service,
            note_search_service,
            workspace_service,
            TargetResolver(note_repo, workspace_service),
            CollectionService(note_repo, note_create_service),
            embedding_profile_service,
            ssh_key_service,
            note_share_link_service,
            PreferencesService(user_repo),
            WorkspaceRemoteService(
                workspace_remote_repo, ssh_key_repo, job_repo, config.workspaces_dir
            ),
            JobService(job_repo),
            EventRepository(db.engine),
            shared_embed_client,
            embed_handler,
            reindex_handler,
            reconcile_links_handler,
            push_handler,
            post_commit_hooks,
        )
    except BaseException:
        db.close()
        raise


def _resources(conn: HTTPConnection) -> AppResources:
    """Resolve the app graph for either scope.

    Typed as HTTPConnection, not Request: FastAPI fills a Request parameter only for
    HTTP requests, so a WebSocket route (`/api/ws`) resolving a Request-typed
    dependency gets it called with no argument at all. HTTPConnection is the common
    base FastAPI injects in both scopes.
    """
    return conn.app.state.resources


def get_job_service(conn: HTTPConnection) -> JobService:
    return _resources(conn).job_service


def get_event_repo(conn: HTTPConnection) -> EventRepository:
    return _resources(conn).event_repo


def get_workspace_remote_service(conn: HTTPConnection) -> WorkspaceRemoteService:
    return _resources(conn).workspace_remote_service


def get_ssh_key_service(conn: HTTPConnection) -> SshKeyService:
    return _resources(conn).ssh_key_service


def get_preferences_service(conn: HTTPConnection) -> PreferencesService:
    return _resources(conn).preferences_service


def get_embedding_profile_service(conn: HTTPConnection) -> EmbeddingProfileService:
    return _resources(conn).embedding_profile_service


def get_folder_meta_repo(conn: HTTPConnection) -> FolderMetaRepository:
    return _resources(conn).folder_meta_repo


def get_note_share_link_repo(conn: HTTPConnection) -> NoteShareLinkRepository:
    return _resources(conn).note_share_link_repo


def get_note_share_link_service(conn: HTTPConnection) -> NoteShareLinkService:
    return _resources(conn).note_share_link_service


def get_note_repo(conn: HTTPConnection) -> NoteRepository:
    return _resources(conn).note_repo


def get_note_create_service(conn: HTTPConnection) -> NoteCreateService:
    return _resources(conn).note_create_service


def get_note_edit_service(conn: HTTPConnection) -> NoteEditService:
    return _resources(conn).note_edit_service


def get_note_delete_service(conn: HTTPConnection) -> NoteDeleteService:
    return _resources(conn).note_delete_service


def get_note_reconcile_service(conn: HTTPConnection) -> NoteReconcileService:
    return _resources(conn).note_reconcile_service


def get_note_tag_service(conn: HTTPConnection) -> NoteTagService:
    return _resources(conn).note_tag_service


def get_note_link_service(conn: HTTPConnection) -> NoteLinkService:
    return _resources(conn).note_link_service


def get_note_folder_service(conn: HTTPConnection) -> NoteFolderService:
    return _resources(conn).note_folder_service


def get_note_temporal_service(conn: HTTPConnection) -> NoteTemporalService:
    return _resources(conn).note_temporal_service


def get_note_version_service(conn: HTTPConnection) -> NoteVersionService:
    return _resources(conn).note_version_service


def get_note_read_service(conn: HTTPConnection) -> NoteReadService:
    return _resources(conn).note_read_service


def get_workspace_service(conn: HTTPConnection) -> WorkspaceService:
    return _resources(conn).workspace_service


def get_target_resolver(conn: HTTPConnection) -> TargetResolver:
    return _resources(conn).target_resolver


def get_user_repo(conn: HTTPConnection) -> UserRepository:
    return _resources(conn).user_repo


def get_session_repo(conn: HTTPConnection) -> SessionRepository:
    return _resources(conn).session_repo


def get_workspace_repo(conn: HTTPConnection) -> WorkspaceRepository:
    return _resources(conn).workspace_repo


def get_oauth_repo(conn: HTTPConnection) -> OAuthRepository:
    return _resources(conn).oauth_repo


def get_provider(conn: HTTPConnection) -> KajetOAuthProvider:
    return _resources(conn).provider


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Narrow, typed identity for REST handlers -- carries only what routes read.
    Built at the HTTP boundary from the session dict; `identity.py`/`get_session_user`
    keep returning `dict` since MCP's context wiring consumes that shape directly."""

    id: str
    email: str
    timezone: str
    locale: str


def get_session_user(request: Request) -> dict | None:
    try:
        session_repo = _resources(request).session_repo
    except AttributeError:
        # Router-level tests may mount a route without constructing an application
        # graph. They are unauthenticated unless they override this named provider.
        return None
    return identity.resolve_session_user_from_cookies(session_repo, request.cookies)


def get_required_user(request: Request) -> CurrentUser:
    user = get_session_user(request)
    if not user:
        log_security_event(
            SecurityEvent.AUTH_FAILURE,
            level="WARNING",
            user_id=None,
            auth_method="session_cookie",
            reason=SecurityReason.NO_SESSION.value,
            **client_ip_fields(request),
        )
        raise HTTPException(status_code=401, detail=AuthError.NOT_AUTHENTICATED)
    return CurrentUser(
        id=user["id"], email=user["email"], timezone=user["timezone"], locale=user["locale"]
    )


def resolve_workspace_target_for(action: str = "workspace.read"):
    """Dependency factory tagging denials with the caller's verb (#281).

    The shared dependency cannot tell a read route from a write route, so every
    denial used to be audited as `workspace.read` — including denied creates,
    deletes and moves. Write routes opt into `workspace.write` explicitly; read
    routes keep the default, so their call sites are untouched.
    """

    def _resolve_workspace_target(
        name: str,
        user: CurrentUser = Depends(get_required_user),
        resolver: TargetResolver = Depends(get_target_resolver),
    ) -> WorkspaceTarget:
        try:
            return resolver.workspace(user.id, name)
        except TargetResolutionError as e:
            audit_denied(
                e.failure,
                action=action,
                resource="workspace",
                caller_id=user.id,
                workspace=name,
            )
            raise HTTPException(status_code=403, detail=AuthError.ACCESS_DENIED) from e

    return _resolve_workspace_target


_resolve_workspace_read = resolve_workspace_target_for("workspace.read")
_resolve_workspace_write = resolve_workspace_target_for("workspace.write")


def resolve_workspace_target(
    name: str,
    user: CurrentUser = Depends(get_required_user),
    resolver: TargetResolver = Depends(get_target_resolver),
) -> WorkspaceTarget:
    return _resolve_workspace_read(name, user, resolver)


def resolve_note_target_for(action: str = "note.read", workspace_action: str = "workspace.read"):
    """Dependency factory tagging denials with the caller's verb (#281).

    Same story as resolve_workspace_target_for: the shared note dependency backs
    both reads and writes (update, move, delete, restore), and every denial was
    audited as `note.read`. Write routes pass `action="note.write"` (and
    `workspace_action="workspace.write"` for the nested workspace check, so a
    workspace denial on a write route is not mislabeled either). Read routes keep
    the defaults.
    """
    ws_dep = (
        _resolve_workspace_write
        if workspace_action == "workspace.write"
        else _resolve_workspace_read
    )

    def _resolve_note_target(
        name: str,
        note_id: str,
        ws: WorkspaceTarget = Depends(ws_dep),
        resolver: TargetResolver = Depends(get_target_resolver),
        user: CurrentUser = Depends(get_required_user),
    ) -> NoteTarget:
        return _resolve_note(name, note_id, ws, resolver, user, action)

    return _resolve_note_target


def _resolve_note(
    name: str,
    note_id: str,
    ws: WorkspaceTarget,
    resolver: TargetResolver,
    user: CurrentUser,
    action: str,
) -> NoteTarget:
    try:
        target = resolver.note(user.id, note_id)
    except TargetResolutionError as e:
        audit_denied(
            e.failure,
            action=action,
            resource="note",
            caller_id=user.id,
            note_id=note_id,
            workspace=name,
        )
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND) from e
    if target.workspace.name != ws.name:
        log_permission_denied(
            action=action,
            resource="note",
            caller_id=user.id,
            reason=SecurityReason.WORKSPACE_MISMATCH,
            note_id=note_id,
            workspace=name,
        )
        raise HTTPException(status_code=404, detail=NoteError.NOT_FOUND)
    return target


def resolve_note_target(
    name: str,
    note_id: str,
    ws: WorkspaceTarget = Depends(resolve_workspace_target),
    resolver: TargetResolver = Depends(get_target_resolver),
    user: CurrentUser = Depends(get_required_user),
) -> NoteTarget:
    """404 before file access: order is fixed by the contract -- 401 (get_required_user)
    -> 403 on URL-workspace access (resolve_workspace_target) -> 404 on note not-found-
    or-not-yours -> 404 on note/URL-workspace mismatch. The mismatch branch is the actual
    fix for the bug this resolver exists for: a note_id from workspace A must not be
    reachable through workspace B's URL just because both belong to the same user."""
    return _resolve_note(name, note_id, ws, resolver, user, "note.read")


# Module-level singletons for write routes (#281). Ruff B008 forbids calling the
# factories inside Depends() defaults, so the two write combinations live here and
# routes reference them directly instead of calling the factory per route.
RESOLVE_WORKSPACE_WRITE = Depends(resolve_workspace_target_for("workspace.write"))
RESOLVE_NOTE_WRITE = Depends(
    resolve_note_target_for("note.write", workspace_action="workspace.write")
)
