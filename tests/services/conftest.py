from collections.abc import Callable
from pathlib import Path

import pytest

from kajet_turbo.db import Database
from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.repositories.notes import (
    NoteChunkRepository,
    NoteLinkRepository,
    NoteRepository,
    NoteTagRepository,
)
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.notes import (
    NoteFolderService,
    NoteLinkService,
    NoteSearchService,
    NoteService,
    NoteTagService,
    NoteVersionService,
)


def build_note_service(
    database: Database,
    indexer=None,
    cache=None,
    link_validation_enabled=None,
    dangling_repo=None,
    query_resolver=None,
    build_embedder=None,
    query_cache=None,
    chunk_repo: NoteChunkRepository | None = None,
) -> NoteService:
    """Construct a fully-wired NoteService from a Database for tests."""
    engine = database.engine
    crud_repo = NoteRepository(engine)
    link_repo = NoteLinkRepository(engine)
    tag_repo = NoteTagRepository(engine)
    if chunk_repo is None:
        chunk_repo = NoteChunkRepository(engine)

    tag_service = NoteTagService(crud_repo, tag_repo, cache)
    link_service = NoteLinkService(crud_repo, link_repo, dangling_repo, link_validation_enabled)
    search_service = NoteSearchService(
        chunk_repo, cache, query_resolver, build_embedder, query_cache
    )
    version_service = NoteVersionService(crud_repo, cache)
    folder_service = NoteFolderService(crud_repo, link_service, cache)

    return NoteService(
        crud_repo,
        link_repo,
        tag_repo,
        chunk_repo,
        tag_service,
        link_service,
        search_service,
        version_service,
        folder_service,
        indexer=indexer,
        cache=cache,
    )


@pytest.fixture
def workspace(git_workspace_factory: Callable[[str], Path]) -> Path:
    return git_workspace_factory("workspace")


@pytest.fixture
def service(database: Database) -> NoteService:
    chunk_repo = NoteChunkRepository(database.engine)
    indexer = NoteIndexer(
        chunk_repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda owner_id: None,  # FTS-only in tests (no network)
        build_embedder=lambda cfg: None,
    )
    return build_note_service(database, indexer=indexer)
