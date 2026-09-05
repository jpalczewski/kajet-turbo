import asyncio
import os
import subprocess
import sys
from pathlib import Path

from kajet_turbo.dependencies import AppConfig
from kajet_turbo.server import build_api_app


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
