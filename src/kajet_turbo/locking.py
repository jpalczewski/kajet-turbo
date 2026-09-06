"""Shared advisory file locking for cross-process serialization.

Kernel-enforced (`flock`) and auto-released on process death, so a crashed
holder never wedges the lock the way a stale marker file would.
"""

import contextlib
import fcntl
import os
import time
from pathlib import Path


@contextlib.contextmanager
def flock_exclusive(lock_path: Path, *, timeout: float | None = None):
    """Hold an exclusive flock on `lock_path` for the duration of the block.

    `timeout=None` blocks indefinitely. A float polls in 50ms steps and raises
    `TimeoutError` once the deadline passes — callers translate that into
    their own domain error.
    """
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if timeout is None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"lock timeout: {lock_path}") from None
                    time.sleep(0.05)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
