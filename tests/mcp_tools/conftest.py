from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastmcp import FastMCP

from kajet_turbo.auth import create_auth
from kajet_turbo.db import Database
from kajet_turbo.embedding.cache import EmbeddingCacheRepository
from kajet_turbo.mcp import build_mcp
from kajet_turbo.repositories.active_workspace import ActiveWorkspaceRepository
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.workspace_meta import WorkspaceMetaRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.workspaces import WorkspaceService


@dataclass
class McpTestContext:
    server: FastMCP
    database: Database
    oauth_repo: OAuthRepository
    workspace_repo: WorkspaceRepository
    active_workspace_repo: ActiveWorkspaceRepository

    def __iter__(self):
        yield self.server
        yield self.database


@pytest.fixture
def mcp_server(database: Database, monkeypatch: pytest.MonkeyPatch) -> McpTestContext:
    from kajet_turbo.repositories.notes import NoteChunkRepository as _NoteChunkRepo
    from tests.services.conftest import build_note_service

    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    note_repository = NoteRepository(database.engine)
    workspace_repository = WorkspaceRepository(database.engine)
    active_workspace_repository = ActiveWorkspaceRepository(database.engine)
    oauth_repository = OAuthRepository(database.engine)
    provider = create_auth(oauth_repository)
    note_chunk_repository = _NoteChunkRepo(database.engine)
    indexer = NoteIndexer(
        note_chunk_repository,
        EmbeddingCacheRepository(database.engine),
        resolve_backend=lambda o: None,
        build_embedder=lambda c: None,
    )
    server = build_mcp(
        build_note_service(database, indexer=indexer),
        WorkspaceService(
            workspace_repository, note_repository, WorkspaceMetaRepository(database.engine)
        ),
        oauth_repository,
        active_workspace_repository,
        provider,
    )
    return McpTestContext(
        server,
        database,
        oauth_repository,
        workspace_repository,
        active_workspace_repository,
    )


@pytest.fixture
def authed_mcp_server(
    mcp_server: McpTestContext, monkeypatch: pytest.MonkeyPatch
) -> McpTestContext:
    """mcp_server with a seeded authenticated user 'u1' (client 'cl1').

    MCP tools resolve identity via get_access_token() -> client_id -> user_id.
    The in-memory Client carries no real token, so we patch get_access_token
    (where it's used) and seed the client_authorizations bridge.
    """
    from datetime import UTC, datetime

    from sqlmodel import Session

    from kajet_turbo.models import User

    with Session(mcp_server.database.engine) as session:
        session.add(
            User(
                id="u1",
                email="u1@test.com",
                password_hash="x",
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        session.commit()
    mcp_server.oauth_repo.record_client_authorization("cl1", "u1")
    mcp_server.workspace_repo.grant_access("u1", "test-ws")
    monkeypatch.setattr(
        "kajet_turbo.mcp.workspaces.get_access_token",
        lambda: SimpleNamespace(client_id="cl1"),
    )
    return mcp_server


@pytest.fixture
def workspaces_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mcp_server: McpTestContext,
    git_workspace_factory: Callable[[str], Path],
) -> Path:
    del mcp_server
    workspace = git_workspace_factory("workspaces/test-ws")
    monkeypatch.setenv("WORKSPACES_DIR", str(workspace.parent))
    return workspace.parent


@pytest.fixture
def authed_workspaces_dir(
    monkeypatch: pytest.MonkeyPatch,
    authed_mcp_server: McpTestContext,
    git_workspace_factory: Callable[[str], Path],
) -> Path:
    """Workspace dir for authenticated user 'u1' — paths are user-scoped:
    WORKSPACES_DIR/u1/test-ws."""
    del authed_mcp_server
    workspace = git_workspace_factory("workspaces/u1/test-ws")
    workspaces_root = workspace.parent.parent  # .../workspaces
    monkeypatch.setenv("WORKSPACES_DIR", str(workspaces_root))
    return workspaces_root
