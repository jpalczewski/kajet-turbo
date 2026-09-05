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
from fastapi import HTTPException
from starlette.requests import Request

from kajet_turbo import identity
from kajet_turbo.auth import KajetOAuthProvider, create_auth
from kajet_turbo.crypto import cipher_for
from kajet_turbo.db import Database
from kajet_turbo.embedding import build_embedder, pooled_embedder_factory
from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository, QueryEmbeddingCache
from kajet_turbo.embedding.client import SharedEmbedderClient
from kajet_turbo.embedding.resolver import ProfileResolver
from kajet_turbo.errors import AuthError
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.embedding_profiles import EmbeddingProfileRepository
from kajet_turbo.repositories.events import EventRepository
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.repositories.git import PostCommitHooks
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
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
    NoteFolderService,
    NoteLinkService,
    NoteSearchService,
    NoteService,
    NoteTagService,
    NoteVersionService,
)
from kajet_turbo.services.push_enqueue import make_enqueue_push_on_commit
from kajet_turbo.services.push_handler import PushHandler
from kajet_turbo.services.reconcile_links_handler import ReconcileLinksHandler
from kajet_turbo.services.reindex_handler import ReindexNoteHandler
from kajet_turbo.services.ssh_keys import SshKeyService
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
    active_workspace_repo: ActiveWorkspaceRepository
    oauth_repo: OAuthRepository
    provider: KajetOAuthProvider
    folder_meta_repo: FolderMetaRepository
    job_repo: JobRepository
    note_service: NoteService
    workspace_service: WorkspaceService
    collection_service: CollectionService
    embedding_profile_service: EmbeddingProfileService
    ssh_key_service: SshKeyService
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
        user_repo = UserRepository(db.engine)
        session_repo = SessionRepository(db.engine)
        workspace_repo = WorkspaceRepository(db.engine)
        active_workspace_repo = ActiveWorkspaceRepository(db.engine)
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
        tag_service = NoteTagService(note_repo, note_tag_repo)
        workspace_service: WorkspaceService
        link_service = NoteLinkService(
            note_repo,
            note_link_repo,
            dangling_repo,
            lambda ws, owner: workspace_service.get_settings(owner, ws)["validate_links"],
            job_repo,
        )
        shared_embed_client = SharedEmbedderClient()
        search_service = NoteSearchService(
            note_chunk_repo,
            profile_resolver.resolve_backend,
            pooled_embedder_factory(),
            QueryEmbeddingCache(),
            note_repo,
            note_tag_repo,
            async_build_embedder=lambda cfg: build_embedder(cfg, shared_embed_client.get()),
        )
        folder_meta_repo = FolderMetaRepository(db.engine)
        folder_service = NoteFolderService(
            note_repo, link_service, folder_meta_repo, reconcile_repo
        )
        note_service = NoteService(
            note_repo,
            note_link_repo,
            note_tag_repo,
            note_chunk_repo,
            tag_service,
            link_service,
            search_service,
            NoteVersionService(note_repo),
            folder_service,
            indexer=indexer,
            reconcile_repo=reconcile_repo,
        )
        ssh_key_repo = SshKeyRepository(db.engine)
        ssh_key_service = SshKeyService(
            ssh_key_repo, lambda: cipher_for("ssh-key", config.secret_key)
        )
        workspace_remote_repo = WorkspaceRemoteRepository(db.engine)
        workspace_service = WorkspaceService(
            workspace_repo,
            note_repo,
            workspace_meta_repo,
            note_service,
            dangling_repo,
            folder_meta_repo,
            workspace_remote_repo,
            active_workspace_repo,
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
            active_workspace_repo,
            oauth_repo,
            provider,
            folder_meta_repo,
            job_repo,
            note_service,
            workspace_service,
            CollectionService(note_repo, note_service),
            embedding_profile_service,
            ssh_key_service,
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


def _resources(request: Request) -> AppResources:
    return request.app.state.resources


def get_job_service(request: Request) -> JobService:
    return _resources(request).job_service


def get_event_repo(request: Request) -> EventRepository:
    return _resources(request).event_repo


def get_workspace_remote_service(request: Request) -> WorkspaceRemoteService:
    return _resources(request).workspace_remote_service


def get_ssh_key_service(request: Request) -> SshKeyService:
    return _resources(request).ssh_key_service


def get_preferences_service(request: Request) -> PreferencesService:
    return _resources(request).preferences_service


def get_embedding_profile_service(request: Request) -> EmbeddingProfileService:
    return _resources(request).embedding_profile_service


def get_folder_meta_repo(request: Request) -> FolderMetaRepository:
    return _resources(request).folder_meta_repo


def get_note_repo(request: Request) -> NoteRepository:
    return _resources(request).note_repo


def get_note_service(request: Request) -> NoteService:
    return _resources(request).note_service


def get_workspace_service(request: Request) -> WorkspaceService:
    return _resources(request).workspace_service


def get_user_repo(request: Request) -> UserRepository:
    return _resources(request).user_repo


def get_session_repo(request: Request) -> SessionRepository:
    return _resources(request).session_repo


def get_workspace_repo(request: Request) -> WorkspaceRepository:
    return _resources(request).workspace_repo


def get_active_workspace_repo(request: Request) -> ActiveWorkspaceRepository:
    return _resources(request).active_workspace_repo


def get_oauth_repo(request: Request) -> OAuthRepository:
    return _resources(request).oauth_repo


def get_provider(request: Request) -> KajetOAuthProvider:
    return _resources(request).provider


def get_session_user(request: Request) -> dict | None:
    try:
        session_repo = _resources(request).session_repo
    except AttributeError:
        # Router-level tests may mount a route without constructing an application
        # graph. They are unauthenticated unless they override this named provider.
        return None
    return identity.resolve_session_user_from_cookies(session_repo, request.cookies)


def get_required_user(request: Request) -> dict:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail=AuthError.NOT_AUTHENTICATED)
    return user
