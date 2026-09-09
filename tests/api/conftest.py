from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI
from starlette.testclient import TestClient

from kajet_turbo.api.errors import install_error_handlers
from kajet_turbo.api.public_notes import router as public_notes_router
from kajet_turbo.api.shared_preview import router as shared_preview_router
from kajet_turbo.api.workspaces import router
from kajet_turbo.db import Database
from kajet_turbo.dependencies import (
    CurrentUser,
    get_note_folder_service,
    get_note_link_service,
    get_note_read_service,
    get_note_reconcile_service,
    get_note_service,
    get_note_share_link_repo,
    get_note_share_link_service,
    get_note_tag_service,
    get_note_temporal_service,
    get_note_version_service,
    get_required_user,
    get_target_resolver,
    get_workspace_service,
)
from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.folder_meta import FolderMetaRepository
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.note_share_link import NoteShareLinkRepository
from kajet_turbo.repositories.notes import NoteLinkRepository, NoteRepository, NoteTagRepository
from kajet_turbo.repositories.workspace_meta import WorkspaceMetaRepository
from kajet_turbo.repositories.workspace_remote import WorkspaceRemoteRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.notes import (
    NoteFolderService,
    NoteLinkService,
    NoteReadService,
    NoteReconcileService,
    NoteService,
    NoteShareLinkService,
    NoteTagService,
    NoteTemporalService,
)
from kajet_turbo.services.targets import TargetResolver
from kajet_turbo.services.workspaces import WorkspaceService


@dataclass
class ApiTestContext:
    client: TestClient
    note_service: NoteService
    workspace: Path
    note_read_service: NoteReadService
    share_link_repo: NoteShareLinkRepository

    def __iter__(self):
        yield self.client
        yield self.note_service
        yield str(self.workspace)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)


def build_test_app(routers: Iterable[APIRouter] = (router,)) -> FastAPI:
    """Same exception-handler wiring as production (`server.py`'s `install_error_handlers`)
    so a route's error contract does not depend on which harness exercises it."""
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    install_error_handlers(app)
    return app


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
        from tests.conftest import seed_user
        from tests.services.conftest import (
            build_note_read_service,
            build_note_service,
        )

        monkeypatch.setenv("WORKSPACES_DIR", str(workspace.parent.parent))
        database = database_factory(f"api-{len(contexts)}.db")
        note_repository = NoteRepository(database.engine)
        workspace_repository = WorkspaceRepository(database.engine)
        note_chunk_repository = _NoteChunkRepo(database.engine)
        note_indexer = NoteIndexer(
            note_chunk_repository,
            EmbeddingCacheRepository(database.engine),
            resolve_backend=lambda owner_id: None,  # FTS-only in tests
            jobs=JobRepository(database.engine),
        )
        note_service = build_note_service(
            database, indexer=note_indexer, chunk_repo=note_chunk_repository
        )
        note_tag_service = NoteTagService(
            note_repository, NoteTagRepository(database.engine), note_indexer
        )
        note_link_service = NoteLinkService(
            note_repository,
            NoteLinkRepository(database.engine),
            NoteTagRepository(database.engine),
            None,
            None,
            JobRepository(database.engine),
        )
        note_folder_service = NoteFolderService(note_repository, note_link_service)
        note_temporal_service = NoteTemporalService(note_repository)
        note_read_service = build_note_read_service(database, indexer=note_indexer)
        note_reconcile_service = NoteReconcileService(
            note_repository,
            NoteLinkRepository(database.engine),
            NoteTagRepository(database.engine),
            note_chunk_repository,
            note_link_service,
            NoteShareLinkRepository(database.engine),
            indexer=note_indexer,
        )
        workspace_service = WorkspaceService(
            workspace_repository,
            note_repository,
            WorkspaceMetaRepository(database.engine),
            note_reconcile_service,
            DanglingLinkRepository(database.engine),
            FolderMetaRepository(database.engine),
            WorkspaceRemoteRepository(database.engine),
            JobRepository(database.engine),
        )
        share_link_repo = NoteShareLinkRepository(database.engine)

        if user_id is not None:
            seed_user(database, user_id)
            if grant_access:
                workspace_repository.grant_access(user_id, "test-ws")

        app = build_test_app(routers=(router, public_notes_router, shared_preview_router))
        app.dependency_overrides[get_note_service] = lambda: note_service
        app.dependency_overrides[get_note_reconcile_service] = lambda: note_reconcile_service
        app.dependency_overrides[get_note_tag_service] = lambda: note_tag_service
        app.dependency_overrides[get_note_link_service] = lambda: note_link_service
        app.dependency_overrides[get_note_folder_service] = lambda: note_folder_service
        app.dependency_overrides[get_note_temporal_service] = lambda: note_temporal_service
        app.dependency_overrides[get_note_version_service] = lambda: note_service._version_service
        app.dependency_overrides[get_note_read_service] = lambda: note_read_service
        app.dependency_overrides[get_workspace_service] = lambda: workspace_service
        app.dependency_overrides[get_note_share_link_repo] = lambda: share_link_repo
        app.dependency_overrides[get_note_share_link_service] = lambda: NoteShareLinkService(
            share_link_repo
        )
        app.dependency_overrides[get_target_resolver] = lambda: TargetResolver(
            note_repository, workspace_service
        )
        if user_id is not None:
            _uid = user_id
            app.dependency_overrides[get_required_user] = lambda: CurrentUser(
                id=_uid, email="", timezone="", locale=""
            )

        client_manager = TestClient(app)
        client = client_manager.__enter__()
        contexts.append((client_manager, None))
        return ApiTestContext(client, note_service, workspace, note_read_service, share_link_repo)

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
