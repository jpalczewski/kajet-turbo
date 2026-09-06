"""Real multi-process HTTP proof for #250: role "mcp" is stateless (#244), so
MCP_WORKERS > 1 must not tie any request to the process that handled a previous one.

Token-rotation and stale-sha conflict *semantics* are already proven at cheaper
layers — see tests/auth/test_provider.py's split-brain suite (two provider instances
sharing one DB) and tests/services/test_notes_sha_gating.py — so this file does not
re-derive that logic. It only proves the real ASGI/uvicorn wiring and the cross-process
fcntl.flock write lock hold across actual separate OS processes, which no in-process
test (even multi-threaded) can demonstrate: threads share one process's memory and one
process's file descriptor table, so a would-be process-local bug wouldn't reproduce.
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from kajet_turbo.db import Database
from kajet_turbo.repositories.oauth import OAuthRepository

_SECRET_KEY = "stress-test-secret"
_USER_ID = "u1"
_CLIENT_ID = "cl1"


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _spawn_mcp_process(*, db_path: Path, workspaces_dir: Path, port: int) -> subprocess.Popen:
    env = {
        **os.environ,
        "KAJET_ROLE": "mcp",
        "MCP_HOST": "127.0.0.1",
        "MCP_PORT": str(port),
        "DB_PATH": str(db_path),
        "WORKSPACES_DIR": str(workspaces_dir),
        "MCP_BASE_URL": f"http://127.0.0.1:{port}",
        "SECRET_KEY": _SECRET_KEY,
    }
    return subprocess.Popen(
        [sys.executable, "-c", "from kajet_turbo.server import main; main()"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _wait_ready(port: int, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode() if proc.stdout else ""
            raise RuntimeError(f"mcp process on port {port} exited early:\n{out}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/readyz", timeout=1.0).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"mcp process on port {port} never became ready")


def _terminate(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    for proc in procs:
        proc.wait(timeout=10)


class RoundRobin:
    """Alternates which process's port answers the next call — stands in for the
    ingress round-robin without a literal proxy process; what matters is that
    consecutive calls are free to land on different backends."""

    def __init__(self, ports: list[int]) -> None:
        self._urls = [f"http://127.0.0.1:{p}/mcp/" for p in ports]
        self._i = 0

    def next_url(self) -> str:
        url = self._urls[self._i % len(self._urls)]
        self._i += 1
        return url


def _seed_authenticated_user(db_path: Path) -> tuple[str, str]:
    """Seeds a user + a matching access/refresh token pair directly, bypassing the
    interactive authorize/consent dance (already covered by the split-brain suite).
    Returns (access_token, refresh_token)."""
    from datetime import UTC, datetime

    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl
    from sqlmodel import Session

    from kajet_turbo.models import User

    db = Database(str(db_path), skip_migrations=True)
    try:
        with Session(db.engine) as session:
            session.add(
                User(
                    id=_USER_ID,
                    email=f"{_USER_ID}@test.com",
                    password_hash="x",
                    created_at=datetime.now(UTC).isoformat(),
                )
            )
            session.commit()
        repo = OAuthRepository(db.engine)
        client_info = OAuthClientInformationFull(
            client_id=_CLIENT_ID,
            redirect_uris=[AnyUrl("http://localhost/callback")],
            token_endpoint_auth_method="none",
        )
        repo.upsert_registered_client(_CLIENT_ID, client_info.model_dump_json())
        repo.record_client_authorization(_CLIENT_ID, _USER_ID)
        access_token, refresh_token = "at-stress", "rt-stress"
        repo.upsert_refresh_token(
            refresh_token, _CLIENT_ID, ["read", "write"], None, user_id=_USER_ID
        )
        repo.upsert_access_token(
            access_token,
            _CLIENT_ID,
            ["read", "write"],
            int(time.time()) + 3600,
            refresh_token,
            user_id=_USER_ID,
        )
        return access_token, refresh_token
    finally:
        db.close()


def _assert_linear_history(workspace_dir: Path, *, min_commits: int) -> None:
    from dulwich.objects import Commit
    from dulwich.repo import Repo

    repo = Repo(str(workspace_dir))
    sha = repo.head()
    seen = 0
    while True:
        commit = repo[sha]
        assert isinstance(commit, Commit)
        assert len(commit.parents) <= 1, "git history forked under concurrent writes"
        seen += 1
        if not commit.parents:
            break
        sha = commit.parents[0]
    assert seen >= min_commits


async def _call(url: str, token: str, tool: str, args: dict) -> dict:
    async with Client(url, auth=token) as client:
        result = await client.call_tool(tool, args)
        return json.loads(result.content[0].text)


@pytest.fixture(params=[1, 2], ids=["workers=1", "workers=2"])
def mcp_cluster(request, tmp_path: Path) -> Iterator[tuple[RoundRobin, Path, Path]]:
    """`request.param` real, separate `mcp`-role processes sharing one DB file and one
    workspaces dir — migrated once up front so no process races Alembic against the
    other. Parametrized so the one-worker configuration proves the same suite passes
    (#250 acceptance)."""
    db_path = tmp_path / "shared.db"
    Database(str(db_path)).close()
    workspaces_dir = tmp_path / "workspaces"
    workspaces_dir.mkdir()

    ports = [_free_port() for _ in range(request.param)]
    procs = [
        _spawn_mcp_process(db_path=db_path, workspaces_dir=workspaces_dir, port=p) for p in ports
    ]
    try:
        for port, proc in zip(ports, procs, strict=True):
            _wait_ready(port, proc)
        yield RoundRobin(ports), db_path, workspaces_dir
    finally:
        _terminate(procs)


def test_oauth_tokens_and_tool_calls_work_across_alternating_processes(mcp_cluster):
    rr, db_path, _ = mcp_cluster
    access_token, refresh_token = _seed_authenticated_user(db_path)

    # Authenticated tool call — may land on either process.
    result = asyncio.run(_call(rr.next_url(), access_token, "create_workspace", {"name": "stress"}))
    assert result["workspace"] == "stress"

    # Refresh grant against the other process: the rotated pair must be usable
    # regardless of which process issued it.
    token_url = rr.next_url() + "token"
    refreshed = httpx.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _CLIENT_ID,
        },
    )
    assert refreshed.status_code == 200, refreshed.text
    new_access_token = refreshed.json()["access_token"]
    assert new_access_token != access_token

    # New token authenticates a tool call on the next process in rotation.
    saved = asyncio.run(
        _call(
            rr.next_url(),
            new_access_token,
            "save_note",
            {"title": "Stress", "content": "hello", "workspace": "stress"},
        )
    )
    assert saved["note_id"]

    # Revoke, then confirm the token is rejected everywhere — not just on whichever
    # process issued or last saw it.
    revoke_url = rr.next_url() + "revoke"
    revoked = httpx.post(
        revoke_url,
        data={"token": new_access_token, "client_id": _CLIENT_ID, "client_secret": ""},
    )
    assert revoked.status_code == 200, revoked.text

    with pytest.raises(Exception):  # noqa: B017 - any rejection proves the revoke took effect
        asyncio.run(
            _call(
                rr.next_url(),
                new_access_token,
                "save_note",
                {
                    "title": "Rejected",
                    "content": "x",
                    "workspace": "stress",
                },
            )
        )


def test_concurrent_edits_across_processes_detect_conflict_not_corruption(mcp_cluster):
    rr, db_path, workspaces_dir = mcp_cluster
    access_token, _ = _seed_authenticated_user(db_path)
    asyncio.run(_call(rr.next_url(), access_token, "create_workspace", {"name": "stress"}))
    saved = asyncio.run(
        _call(
            rr.next_url(),
            access_token,
            "save_note",
            {"title": "Racy", "content": "v0", "workspace": "stress"},
        )
    )
    note_id = saved["note_id"]
    sha = asyncio.run(_call(rr.next_url(), access_token, "get_note", {"note_id": note_id}))["sha"]

    def edit(i: int) -> dict:
        url = rr.next_url()
        return asyncio.run(
            _call(
                url,
                access_token,
                "edit_note",
                {
                    "note_id": note_id,
                    "expected_sha": sha,
                    "content": f"v{i}",
                },
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, range(2)))

    # A stale edit returns StaleVersion ({note_id, error}); a successful one returns
    # EditNoteSuccess ({note_id, replaced, warnings, ...}) with no "error" key.
    succeeded = [r for r in results if "error" not in r]
    stale = [r for r in results if "error" in r]
    assert len(succeeded) == 1, results
    assert len(stale) == 1, results

    _assert_linear_history(workspaces_dir / _USER_ID / "stress", min_commits=2)


def test_process_restart_mid_stream_is_not_a_session_not_found():
    """Dedicated 2-process setup: kill one process mid-flight, then send the next
    request through the round-robin. stateless_http (#244) means the survivor must
    answer normally instead of surfacing a session-affinity error."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "shared.db"
        Database(str(db_path)).close()
        workspaces_dir = tmp_path / "workspaces"
        workspaces_dir.mkdir()
        ports = [_free_port(), _free_port()]
        procs = [
            _spawn_mcp_process(db_path=db_path, workspaces_dir=workspaces_dir, port=p)
            for p in ports
        ]
        try:
            for port, proc in zip(ports, procs, strict=True):
                _wait_ready(port, proc)
            rr = RoundRobin(ports)
            access_token, _ = _seed_authenticated_user(db_path)

            asyncio.run(_call(rr.next_url(), access_token, "create_workspace", {"name": "stress"}))

            procs[0].terminate()
            procs[0].wait(timeout=10)

            # Next call must succeed on whichever process is still up — no
            # "no valid session"/404, and no dependency on which process created
            # the workspace.
            result = asyncio.run(
                _call(
                    rr.next_url(),
                    access_token,
                    "save_note",
                    {"title": "After restart", "content": "still here", "workspace": "stress"},
                )
            )
            assert result["note_id"]
        finally:
            _terminate([p for p in procs if p.poll() is None])
