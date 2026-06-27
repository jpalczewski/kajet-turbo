from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlmodel import Session
from starlette.testclient import TestClient

from kajet_turbo.api.workspaces import router
from kajet_turbo.db import Database
from kajet_turbo.dependencies import get_note_service, get_workspace_service
from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.models import User
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.workspace_meta import WorkspaceMetaRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


@dataclass
class ApiTestContext:
    client: TestClient
    note_service: NoteService
    workspace: Path

    def __iter__(self):
        yield self.client
        yield self.note_service
        yield str(self.workspace)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)


@pytest.fixture
def workspace(git_workspace_factory: Callable[[str], Path]) -> Path:
    return git_workspace_factory("workspaces/u1/test-ws")


@pytest.fixture
def api_client_factory(
    database_factory: Callable[..., Database],
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., ApiTestContext]]:
    contexts: list[tuple[TestClient, Any]] = []

    def create(*, user_id: str | None = "u1", grant_access: bool = True) -> ApiTestContext:
        from kajet_turbo.repositories.notes import NoteChunkRepository as _NoteChunkRepo
        from tests.services.conftest import build_note_service

        monkeypatch.setenv("WORKSPACES_DIR", str(workspace.parent.parent))
        database = database_factory(f"api-{len(contexts)}.db")
        note_repository = NoteRepository(database.engine)
        workspace_repository = WorkspaceRepository(database.engine)
        note_chunk_repository = _NoteChunkRepo(database.engine)
        note_indexer = NoteIndexer(
            note_chunk_repository,
            EmbeddingCacheRepository(database.engine),
            resolve_backend=lambda owner_id: None,  # FTS-only in tests
            build_embedder=lambda cfg: None,
        )
        note_service = build_note_service(database, indexer=note_indexer)
        workspace_service = WorkspaceService(
            workspace_repository, note_repository, WorkspaceMetaRepository(database.engine)
        )

        if user_id is not None:
            with Session(database.engine) as session:
                session.add(
                    User(
                        id=user_id,
                        email=f"{user_id}@test.com",
                        password_hash="x",
                        created_at="2026-01-01",
                    )
                )
                session.commit()
            if grant_access:
                workspace_repository.grant_access(user_id, "test-ws")

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_note_service] = lambda: note_service
        app.dependency_overrides[get_workspace_service] = lambda: workspace_service
        monkeypatch.setattr(
            "kajet_turbo.api.workspaces.get_session_user",
            lambda _request: {"id": user_id} if user_id is not None else None,
        )

        client_manager = TestClient(app)
        client = client_manager.__enter__()
        contexts.append((client_manager, None))
        return ApiTestContext(client, note_service, workspace)

    yield create

    for client_manager, *_ in reversed(contexts):
        client_manager.__exit__(None, None, None)


@pytest.fixture
def auth_client(api_client_factory: Callable[..., ApiTestContext]) -> ApiTestContext:
    return api_client_factory()


@pytest.fixture
def no_access_client(api_client_factory: Callable[..., ApiTestContext]) -> ApiTestContext:
    return api_client_factory(grant_access=False)


@pytest.fixture
def anon_client(api_client_factory: Callable[..., ApiTestContext]) -> ApiTestContext:
    return api_client_factory(user_id=None)
