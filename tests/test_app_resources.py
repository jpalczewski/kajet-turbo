import asyncio
import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from kajet_turbo import dependencies, server
from kajet_turbo.dependencies import AppConfig, Database
from kajet_turbo.server import build_api_app, build_app, build_mcp_app


def _resources(app):
    return getattr(app, "_app", app).state.resources


def test_imports_do_not_construct_resources_or_require_secrets(tmp_path: Path):
    missing_parent = tmp_path / "does-not-exist"
    env = {
        **os.environ,
        "DB_PATH": str(missing_parent / "kajet.db"),
        "SECRET_KEY": "",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import kajet_turbo.dependencies, kajet_turbo.server, kajet_turbo.mcp",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not missing_parent.exists()


def test_free_threading_survives_fastmcp_4_import(tmp_path: Path):
    """#244: FastMCP 4 / MCP SDK v2 must not force the GIL back on. A subprocess, not
    the already-imported test process, since the GIL is fixed for a process's whole
    lifetime — an already-running interpreter would report yesterday's answer."""
    env = {**os.environ, "DB_PATH": str(tmp_path / "kajet.db")}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, kajet_turbo.server; "
            "assert sys._is_gil_enabled() is False, 'GIL re-enabled after import'",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_two_apps_own_distinct_resources_and_disposal_is_isolated(tmp_path: Path):
    app_a = build_api_app(
        AppConfig(
            db_path=str(tmp_path / "a.db"),
            workspaces_dir=str(tmp_path / "workspaces-a"),
            mcp_base_url="http://a.test",
            secret_key="a-secret",
        )
    )
    app_b = build_api_app(
        AppConfig(
            db_path=str(tmp_path / "b.db"),
            workspaces_dir=str(tmp_path / "workspaces-b"),
            mcp_base_url="http://b.test",
            secret_key="b-secret",
        )
    )
    resources_a = _resources(app_a)
    resources_b = _resources(app_b)

    assert resources_a.db is not resources_b.db
    assert resources_a.workspace_service.workspace_path("u", "notes").endswith(
        "workspaces-a/u/notes"
    )
    assert resources_b.workspace_service.workspace_path("u", "notes").endswith(
        "workspaces-b/u/notes"
    )
    assert str(resources_a.provider.base_url).startswith("http://a.test")
    assert str(resources_b.provider.base_url).startswith("http://b.test")

    resources_a.user_repo.create("a@example.test", "hash")
    assert resources_a.user_repo.count() == 1
    assert resources_b.user_repo.count() == 0

    asyncio.run(resources_a.aclose())
    assert resources_b.user_repo.count() == 0
    asyncio.run(resources_b.aclose())


def test_worker_finishes_before_database_closes_in_all_role(tmp_path: Path):
    """Regression for #245: closing the engine while the worker thread is still
    alive corrupts a live session. A timed thread.join() would let this pass."""
    app = build_app(
        AppConfig(
            db_path=str(tmp_path / "all.db"),
            workspaces_dir=str(tmp_path / "workspaces"),
            mcp_base_url="http://localhost",
        )
    )
    resources = _resources(app)
    worker_alive_at_close = []
    original_close = resources.db.close

    def spy_close():
        worker_alive_at_close.append(
            any(t.name == "kajet-inprocess-worker" for t in threading.enumerate())
        )
        original_close()

    resources.db.close = spy_close

    with TestClient(app):
        pass

    assert worker_alive_at_close == [False]


def test_build_resources_closes_db_on_partial_construction_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def boom(*args, **kwargs):
        raise RuntimeError("auth construction boom")

    monkeypatch.setattr(dependencies, "create_auth", boom)
    closed: list[bool] = []
    original_close = Database.close

    def spy_close(self) -> None:
        closed.append(True)
        original_close(self)

    monkeypatch.setattr(Database, "close", spy_close)

    with pytest.raises(RuntimeError, match="auth construction boom"):
        dependencies.build_resources(AppConfig(db_path=str(tmp_path / "partial.db")))

    assert closed == [True]


def test_build_mcp_app_closes_resources_on_assembly_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def boom(resources):
        raise RuntimeError("mcp assembly boom")

    monkeypatch.setattr(server, "build_mcp", boom)
    closed: list[bool] = []
    original_close = Database.close

    def spy_close(self) -> None:
        closed.append(True)
        original_close(self)

    monkeypatch.setattr(Database, "close", spy_close)

    with pytest.raises(RuntimeError, match="mcp assembly boom"):
        build_mcp_app(
            AppConfig(db_path=str(tmp_path / "assembly.db"), mcp_base_url="http://localhost")
        )

    assert closed == [True]


def test_lifespan_startup_failure_releases_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A later composed lifespan failing to start must still release resources
    the earlier ones (here _app_lifespan) already acquired."""

    @asynccontextmanager
    async def boom(app):
        raise RuntimeError("sweep boom")
        yield  # pragma: no cover - never reached

    monkeypatch.setattr(server, "_sweep_outbox_lifespan", boom)

    app = build_app(
        AppConfig(
            db_path=str(tmp_path / "startup.db"),
            workspaces_dir=str(tmp_path / "workspaces-startup"),
            mcp_base_url="http://localhost",
        )
    )
    resources = _resources(app)

    # Starlette's TestClient runs the ASGI lifespan through an anyio TaskGroup,
    # which reports the failure wrapped in an ExceptionGroup rather than raising
    # RuntimeError directly.
    with pytest.raises(BaseException) as exc_info, TestClient(app):
        pass

    assert "sweep boom" in repr(exc_info.value)
    assert resources._closed


def test_resources_can_be_disposed_without_startup(tmp_path: Path):
    """An app assembled but never started still has an explicit disposal path."""
    app = build_mcp_app(
        AppConfig(db_path=str(tmp_path / "unstarted.db"), mcp_base_url="http://localhost")
    )
    resources = app.state.resources

    asyncio.run(resources.aclose())

    assert resources._closed
