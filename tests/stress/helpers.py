"""Process helpers shared by the real-supervisor stress tests."""

import socket
import subprocess
import time
from contextlib import closing

import httpx2


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(port: int, proc: subprocess.Popen, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode() if proc.stdout else ""
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
