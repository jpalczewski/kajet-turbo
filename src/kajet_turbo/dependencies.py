import asyncio
import functools
import os

import httpx
from fastapi import HTTPException
from starlette.requests import Request

from kajet_turbo import identity
from kajet_turbo.auth import KajetOAuthProvider, create_auth
from kajet_turbo.cache import WorkspaceCache, cache_enabled
from kajet_turbo.crypto import cipher_for, cipher_from_env
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
from kajet_turbo.repositories.git import register_post_commit_hook
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
from kajet_turbo.services.ssh_keys import SshKeyService
from kajet_turbo.services.workspace_remote import WorkspaceRemoteService
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.workspace import WORKSPACES_DIR

db = Database()
note_repo = NoteRepository(db.engine)
note_link_repo = NoteLinkRepository(db.engine)
note_tag_repo = NoteTagRepository(db.engine)
note_chunk_repo = NoteChunkRepository(db.engine)
user_repo = UserRepository(db.engine)
session_repo = SessionRepository(db.engine)
workspace_repo = WorkspaceRepository(db.engine)
active_workspace_repo = ActiveWorkspaceRepository(db.engine)
oauth_repo = OAuthRepository(db.engine)
provider: KajetOAuthProvider = create_auth(oauth_repo)

_profile_repo = EmbeddingProfileRepository(db.engine)


@functools.cache
def _profile_cipher():
    # Lazy: cipher_from_env needs SECRET_KEY. Building it here (not at import) keeps module
    # import / app boot from hard-requiring SECRET_KEY; only needed to seal/unseal a key.
    # Memoized — KeyCipher's scrypt derivation is cached too.
    return cipher_from_env()


_profile_resolver = ProfileResolver(_profile_repo, _profile_cipher)


def _probe_dim(base_url: str, model: str, api_key: str | None) -> int:
    """Embed a probe string against a candidate profile to validate it and capture its dim.
    Runs the async embedder via asyncio.run (sync call site, no running loop)."""
    cfg = EmbedderConfig(
        backend_id=base_url, type="openai", model=model, dim=0, base_url=base_url, api_key=api_key
    )

    async def _run() -> int:
        async with httpx.AsyncClient(timeout=30.0) as client:
            vec = await build_embedder(cfg, client).embed_query("probe")
        return len(vec)

    return asyncio.run(_run())


embedding_profile_service = EmbeddingProfileService(_profile_repo, _profile_cipher, _probe_dim)

job_repo = JobRepository(db.engine)

# Write path persists chunks + FTS inline; the embedding HTTP roundtrip is deferred
# to an embed_note job (handled by the worker via embed_handler below).
note_indexer = NoteIndexer(
    repo=note_chunk_repo,
    cache=EmbeddingCacheRepository(db.engine),
    resolve_backend=_profile_resolver.resolve_backend,
    enqueue_embed=make_enqueue_embed(job_repo),
)

embed_handler = EmbedNoteHandler(
    chunk_repo=note_chunk_repo,
    cache=EmbeddingCacheRepository(db.engine),
    resolve_backend=_profile_resolver.resolve_backend,
    build_embedder=pooled_embedder_factory(),
)

_query_cache = QueryEmbeddingCache()

workspace_meta_repo = WorkspaceMetaRepository(db.engine)
dangling_repo = DanglingLinkRepository(db.engine)
link_reconcile_repo = LinkReconcileRepository(db.engine, job_repo)

_cache = WorkspaceCache() if cache_enabled() else None
_link_validation = lambda ws, owner: workspace_service.get_settings(owner, ws)["validate_links"]  # noqa: E731

_note_tag_service = NoteTagService(note_repo, note_tag_repo, _cache, indexer=note_indexer)
_note_link_service = NoteLinkService(note_repo, note_link_repo, dangling_repo, _link_validation)
# Long-lived client for query embedding: keep-alive across searches kills the
# per-call TCP+TLS connect tail. Closed in the app lifespan (server.py).
shared_embed_client = SharedEmbedderClient()

_note_search_service = NoteSearchService(
    note_chunk_repo,
    _cache,
    _profile_resolver.resolve_backend,
    pooled_embedder_factory(),
    _query_cache,
    note_repo,
    note_tag_repo,
    async_build_embedder=lambda cfg: build_embedder(cfg, shared_embed_client.get()),
)
_note_version_service = NoteVersionService(note_repo, _cache)
folder_meta_repo = FolderMetaRepository(db.engine)
_note_folder_service = NoteFolderService(
    note_repo,
    _note_link_service,
    _cache,
    folder_meta_repo,
    link_reconcile_repo,
)

note_service = NoteService(
    note_repo,
    note_link_repo,
    note_tag_repo,
    note_chunk_repo,
    _note_tag_service,
    _note_link_service,
    _note_search_service,
    _note_version_service,
    _note_folder_service,
    indexer=note_indexer,
    cache=_cache,
    reconcile_repo=link_reconcile_repo,
)

_ssh_key_repo = SshKeyRepository(db.engine)
ssh_key_service = SshKeyService(_ssh_key_repo, lambda: cipher_for("ssh-key"))

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
    cache=_cache,
    reconcile_repo=link_reconcile_repo,
)
push_handler = PushHandler(
    workspace_remote_repo,
    _ssh_key_repo,
    lambda: cipher_for("ssh-key"),
    known_hosts_path=os.getenv("KAJET_KNOWN_HOSTS", "/data/ssh/known_hosts"),
    key_dir=os.getenv("KAJET_KEY_TMPDIR", "/dev/shm"),
)

# Enqueue an auto-push after every commit in a workspace that has an enabled remote.
# Registered at import so it is active in the API/MCP processes that perform commits.
register_post_commit_hook(
    make_enqueue_push_on_commit(job_repo, workspace_remote_repo, WORKSPACES_DIR)
)

reconcile_links_handler = ReconcileLinksHandler(
    note_repo,
    _note_link_service,
    dangling_repo,
    link_reconcile_repo,
    WORKSPACES_DIR,
)

workspace_remote_service = WorkspaceRemoteService(
    workspace_remote_repo, _ssh_key_repo, job_repo, WORKSPACES_DIR
)

job_service = JobService(job_repo)

event_repo = EventRepository(db.engine)


def get_job_service() -> JobService:
    return job_service


def get_event_repo() -> EventRepository:
    return event_repo


def get_workspace_remote_service() -> WorkspaceRemoteService:
    return workspace_remote_service


def get_ssh_key_service() -> SshKeyService:
    return ssh_key_service


def get_embedding_profile_service() -> EmbeddingProfileService:
    return embedding_profile_service


def get_folder_meta_repo() -> FolderMetaRepository:
    return folder_meta_repo


def get_note_repo() -> NoteRepository:
    return note_repo


def get_note_service() -> NoteService:
    return note_service


def get_workspace_service() -> WorkspaceService:
    return workspace_service


def get_user_repo() -> UserRepository:
    return user_repo


def get_session_repo() -> SessionRepository:
    return session_repo


def get_workspace_repo() -> WorkspaceRepository:
    return workspace_repo


def get_active_workspace_repo() -> ActiveWorkspaceRepository:
    return active_workspace_repo


def get_oauth_repo() -> OAuthRepository:
    return oauth_repo


def get_provider() -> KajetOAuthProvider:
    return provider


def get_session_user(request: Request) -> dict | None:
    return identity.resolve_session_user(
        session_repo, identity.session_token_from_cookies(request.cookies)
    )


def get_required_user(request: Request) -> dict:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail=AuthError.NOT_AUTHENTICATED)
    return user
