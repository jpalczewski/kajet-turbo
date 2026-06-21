from collections.abc import Callable
from pathlib import Path

import pytest

from kajet_turbo.db import Database
from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.notes import NoteService


@pytest.fixture
def workspace(git_workspace_factory: Callable[[str], Path]) -> Path:
    return git_workspace_factory("workspace")


@pytest.fixture
def service(database: Database) -> NoteService:
    repo = NoteRepository(database.engine)
    indexer = NoteIndexer(
        repo,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda owner_id: None,  # FTS-only in tests (no network)
        build_embedder=lambda cfg: None,
    )
    return NoteService(repo, indexer=indexer)
