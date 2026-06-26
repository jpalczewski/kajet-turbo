import asyncio
import functools
import os

import httpx
from starlette.requests import Request

from kajet_turbo.auth import KajetOAuthProvider, create_auth
from kajet_turbo.cache import WorkspaceCache, cache_enabled
from kajet_turbo.crypto import cipher_for, cipher_from_env
from kajet_turbo.db import Database
from kajet_turbo.embedding import build_embedder, pooled_embedder_factory
from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.embedding.cache import EmbeddingCacheRepository, QueryEmbeddingCache
from kajet_turbo.embedding.resolver import ProfileResolver
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.embedding_profiles import EmbeddingProfileRepository
from kajet_turbo.repositories.git import register_post_commit_hook
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.sessions import SessionRepository
from kajet_turbo.repositories.ssh_keys import SshKeyRepository
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.repositories.workspace_meta import WorkspaceMetaRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.services.embedding_profiles import EmbeddingProfileService
from kajet_turbo.services.heal_enqueue import make_enqueue_heal_on_commit
from kajet_turbo.services.heal_handler import HealDanglingHandler
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.jobs import JobService
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.push_enqueue import make_enqueue_push_on_commit
from kajet_turbo.services.push_handler import PushHandler
from kajet_turbo.services.ssh_keys import SshKeyService
from kajet_turbo.services.workspace_remote import WorkspaceRemoteService
from kajet_turbo.services.workspaces import WorkspaceService
from kajet_turbo.workspace import WORKSPACES_DIR

db = Database()
note_repo = NoteRepository(db.engine)
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

note_indexer = NoteIndexer(
    repo=note_repo,
    cache=EmbeddingCacheRepository(db.engine),
    resolve_backend=_profile_resolver.resolve_backend,
    build_embedder=pooled_embedder_factory(),
)

_query_cache = QueryEmbeddingCache()

workspace_meta_repo = WorkspaceMetaRepository(db.engine)
workspace_service = WorkspaceService(workspace_repo, note_repo, workspace_meta_repo)
dangling_repo = DanglingLinkRepository(db.engine)

note_service = NoteService(
    note_repo,
    cache=WorkspaceCache() if cache_enabled() else None,
    indexer=note_indexer,
    query_resolver=_profile_resolver.resolve_backend,
    build_embedder=pooled_embedder_factory(),
    query_cache=_query_cache,
    link_validation_enabled=lambda ws, owner: workspace_service.get_settings(owner, ws)[
        "validate_links"
    ],
    dangling_repo=dangling_repo,
)

_ssh_key_repo = SshKeyRepository(db.engine)
ssh_key_service = SshKeyService(_ssh_key_repo, lambda: cipher_for("ssh-key"))

job_repo = JobRepository(db.engine)
workspace_remote_repo = WorkspaceRemoteRepository(db.engine)
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

heal_handler = HealDanglingHandler(note_repo, dangling_repo)
# Enqueue a dangling-link heal after every commit in a workspace that has dangling rows.
# Zero-cost for validation-on workspaces (the EXISTS check short-circuits immediately).
register_post_commit_hook(make_enqueue_heal_on_commit(job_repo, dangling_repo, WORKSPACES_DIR))

workspace_remote_service = WorkspaceRemoteService(
    workspace_remote_repo, _ssh_key_repo, job_repo, WORKSPACES_DIR
)

job_service = JobService(job_repo)


def get_job_service() -> JobService:
    return job_service


def get_workspace_remote_service() -> WorkspaceRemoteService:
    return workspace_remote_service


def get_ssh_key_service() -> SshKeyService:
    return ssh_key_service


def get_embedding_profile_service() -> EmbeddingProfileService:
    return embedding_profile_service


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
    token = request.cookies.get("kajet_session", "")
    return session_repo.get_user(token) if token else None
