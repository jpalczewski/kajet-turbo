"""Process helpers shared by the real-supervisor stress tests."""

import socket
import subprocess
import time
from contextlib import closing
from pathlib import Path

import httpx2


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def spawn_logged(args: list[str], *, env: dict[str, str], log_path: Path) -> subprocess.Popen:
    """Start a server process with its combined output going to ``log_path``.

    Never a pipe: nothing drains one while the test runs, and once the 64 KiB kernel
    buffer is full a child's next log write blocks its event loop mid-request. Every
    request logs an ``http`` access line (~330 B), so a pipe wedges the server about
    190 requests in.
    """
    with log_path.open("ab") as log:
        return subprocess.Popen(args, env=env, stdout=log, stderr=subprocess.STDOUT)


def wait_ready(port: int, proc: subprocess.Popen, *, log_path: Path, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = log_path.read_text(errors="replace")
            raise RuntimeError(f"process on port {port} exited early:\n{out}")
        try:
            if httpx2.get(f"http://127.0.0.1:{port}/readyz", timeout=1.0).status_code == 200:
                return
        except httpx2.TransportError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"process on port {port} never became ready")


def terminate(procs: list[subprocess.Popen], grace: float = 30.0) -> None:
    """SIGTERM every process, then SIGKILL any that outlive ``grace``. A graceful
    shutdown drains in-flight children and slows down badly on a loaded runner; raising
    ``TimeoutExpired`` here would leak the process and mask the test's own failure."""
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    for proc in procs:
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
