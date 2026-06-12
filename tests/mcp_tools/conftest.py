from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastmcp import FastMCP

from kajet_turbo.auth import create_auth
from kajet_turbo.db import Database
from kajet_turbo.mcp import build_mcp
from kajet_turbo.repositories.notes import NoteRepository
from kajet_turbo.repositories.oauth import OAuthRepository
from kajet_turbo.repositories.workspaces import WorkspaceRepository
from kajet_turbo.services.notes import NoteService
from kajet_turbo.services.workspaces import WorkspaceService


@dataclass
class McpTestContext:
    server: FastMCP
    database: Database

    def __iter__(self):
        yield self.server
        yield self.database


@pytest.fixture
def mcp_server(database: Database, monkeypatch: pytest.MonkeyPatch) -> McpTestContext:
    monkeypatch.setenv("MCP_BASE_URL", "http://localhost:8000")
    note_repository = NoteRepository(database.engine)
    workspace_repository = WorkspaceRepository(database.engine)
    oauth_repository = OAuthRepository(database.engine)
    provider = create_auth(oauth_repository)
    server = build_mcp(
        NoteService(note_repository),
        WorkspaceService(workspace_repository, note_repository),
        oauth_repository,
        provider,
    )
    return McpTestContext(server, database)


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
