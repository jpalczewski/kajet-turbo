import os
import sysconfig
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

# Some test modules import sqlalchemy before kajet_turbo, bypassing the guard
# in kajet_turbo/__init__.py — set it here too (conftest runs first), so the
# GIL stays disabled on free-threaded builds.
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    os.environ.setdefault("DISABLE_SQLALCHEMY_CEXT_RUNTIME", "1")

# kajet_turbo.dependencies runs Database() and create_auth() at module level on import.
# Both need env vars set before test files are collected by pytest.
if "DB_PATH" not in os.environ:
    _db_fd, _db_path = tempfile.mkstemp(suffix=".db")
    os.close(_db_fd)  # SQLite reopens the (empty) file itself
    os.environ["DB_PATH"] = _db_path
if "MCP_BASE_URL" not in os.environ:
    os.environ["MCP_BASE_URL"] = "http://localhost:8000"

from kajet_turbo.db import Database
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.workspace import note_filepath, write_note_file


@pytest.fixture
def database_factory(tmp_path: Path) -> Iterator[Callable[..., Database]]:
    databases: list[Database] = []

    def create(name: str = "test.db", *, embedding_dim: int | None = None) -> Database:
        previous_dim = os.environ.get("EMBEDDING_DIM")
        if embedding_dim is not None:
            os.environ["EMBEDDING_DIM"] = str(embedding_dim)
        try:
            database = Database(str(tmp_path / name))
        finally:
            if embedding_dim is not None:
                if previous_dim is None:
                    os.environ.pop("EMBEDDING_DIM", None)
                else:
                    os.environ["EMBEDDING_DIM"] = previous_dim
        databases.append(database)
        return database

    yield create

    for database in databases:
        database.close()


@pytest.fixture
def database(database_factory: Callable[..., Database]) -> Database:
    return database_factory()


@pytest.fixture
def git_workspace_factory(tmp_path: Path) -> Callable[[str], Path]:
    def create(relative_path: str = "workspace") -> Path:
        workspace = tmp_path / relative_path
        workspace.mkdir(parents=True, exist_ok=True)
        GitRepository.init(str(workspace))
        return workspace

    return create


@pytest.fixture
def note_file_factory() -> Callable[..., str]:
    def create(
        workspace: str | Path,
        title: str = "Test Note",
        *,
        note_id: str = "note-001",
        folder: str = "",
        tags: list[str] | None = None,
        content: str = "Test content",
        created_at: str = "2026-01-01T00:00:00+00:00",
        updated_at: str = "2026-01-01T00:00:00+00:00",
    ) -> str:
        path = note_filepath(str(workspace), folder, title)
        write_note_file(
            path,
            note_id,
            title,
            tags or [],
            created_at,
            updated_at,
            content,
        )
        return path

    return create
