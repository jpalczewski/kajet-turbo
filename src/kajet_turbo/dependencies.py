import functools

from starlette.requests import Request

from kajet_turbo.auth import KajetOAuthProvider, create_auth
from kajet_turbo.cache import WorkspaceCache, cache_enabled
from kajet_turbo.db import Database
from kajet_turbo.embedding import pooled_embedder_factory
from kajet_turbo.embedding.cache import EmbeddingCacheRepository, QueryEmbeddingCache
from kajet_turbo.embedding.resolver import BackendResolver, resolver_from_env
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.sessions import SessionRepository
from kajet_turbo.repositories.users import UserRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService

db = Database()
note_repo = NoteRepository(db.engine)
user_repo = UserRepository(db.engine)
session_repo = SessionRepository(db.engine)
workspace_repo = WorkspaceRepository(db.engine)
oauth_repo = OAuthRepository(db.engine)
provider: KajetOAuthProvider = create_auth(oauth_repo)


# resolver_from_env builds the key cipher, which requires SECRET_KEY. Resolve it lazily
# so importing this module (done at app startup and in tests) never hard-requires
# SECRET_KEY — it's only needed when a note is actually indexed against a sealed key.
@functools.cache
def _backend_resolver() -> BackendResolver:
    return resolver_from_env(db.engine)


note_indexer = NoteIndexer(
    repo=note_repo,
    cache=EmbeddingCacheRepository(db.engine),
    resolve_backend=lambda user_id: _backend_resolver().resolve_backend(user_id),
    build_embedder=pooled_embedder_factory(),
)

_query_cache = QueryEmbeddingCache()

note_service = NoteService(
    note_repo,
    cache=WorkspaceCache() if cache_enabled() else None,
    indexer=note_indexer,
    query_resolver=lambda user_id: _backend_resolver().resolve_backend(user_id),
    build_embedder=pooled_embedder_factory(),
    query_cache=_query_cache,
)
workspace_service = WorkspaceService(workspace_repo, note_repo)


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


def get_oauth_repo() -> OAuthRepository:
    return oauth_repo


def get_provider() -> KajetOAuthProvider:
    return provider


def get_session_user(request: Request) -> dict | None:
    token = request.cookies.get("kajet_session", "")
    return session_repo.get_user(token) if token else None
