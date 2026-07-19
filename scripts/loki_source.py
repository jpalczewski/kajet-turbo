"""Query kajet-turbo logs from Loki (niechybnie, loopback-only port 3100 via SSH tunnel).

Used by analyze-logs.py as the default event source. See
docs/superpowers/specs/2026-07-19-loki-log-tooling-design.md for the design.
"""

from __future__ import annotations

import atexit
import json
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SSH_HOST = "178.104.253.119"
SSH_USER = "dyzurny"
SSH_KEY = "~/.ssh/niechybnie_niechybnie_dyzurny"

LEVEL_ORDER = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}

_ENV_ALIASES = {
    "prod": "produkcja",
    "production": "produkcja",
    "dev": "develop",
    "development": "develop",
}


def _normalize_env(env: str) -> str:
    return _ENV_ALIASES.get(env, env)


def build_selector(
    role: str,
    env: str,
    min_level: str | None = None,
    msg_filter: list[str] | None = None,
) -> str:
    """Build a LogQL label selector for kajet-turbo logs.

    Pushes level/msg filtering into the selector since both are Loki labels
    (see the Alloy pipeline's discovery.relabel + stage.labels config in the
    niechybnie repo) — everything else (--grep, --fields) stays client-side
    in analyze-logs.py, same as it is today.
    """
    parts = [
        'coolify_projectName="kajet-turbo"',
        f'container=~"kajet-{role}-.*"',
        f'coolify_environmentName="{_normalize_env(env)}"',
    ]
    if min_level:
        # default-if-unrecognized matches mode_errors()'s own LEVEL_ORDER.get(min_level, 2)
        threshold = LEVEL_ORDER.get(min_level, 2)
        levels = [lvl for lvl, order in LEVEL_ORDER.items() if order >= threshold]
        parts.append(f'level=~"{"|".join(levels)}"')
    if msg_filter:
        parts.append(f'msg=~"{"|".join(msg_filter)}"')
    return "{" + ", ".join(parts) + "}"


def parse_query_range_response(data: dict[str, Any]) -> list[dict]:
    """Flatten a Loki query_range response into a time-sorted list[dict].

    Mirrors analyze-logs.py's parse_log(): tolerant of non-JSON lines (skipped),
    tolerant of a non-JSON prefix before the first '{' on a line.
    """
    rows: list[tuple[int, dict]] = []
    for stream in data.get("data", {}).get("result", []):
        for ts_ns, line in stream.get("values", []):
            idx = line.find("{")
            if idx == -1:
                continue
            try:
                event = json.loads(line[idx:])
            except json.JSONDecodeError:
                continue
            rows.append((int(ts_ns), event))
    rows.sort(key=lambda r: r[0])
    return [event for _, event in rows]


class LokiUnreachableError(RuntimeError):
    pass


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class _Tunnel:
    def __init__(self) -> None:
        self.port = _free_local_port()
        self.proc = subprocess.Popen(
            [
                "ssh",
                "-f",
                "-N",
                "-L",
                f"{self.port}:localhost:3100",
                "-i",
                SSH_KEY,
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "ExitOnForwardFailure=yes",
                f"{SSH_USER}@{SSH_HOST}",
            ],
        )
        self.proc.wait()  # -f backgrounds after auth; wait() reaps the launcher, not the tunnel
        if self.proc.returncode != 0:
            raise LokiUnreachableError(
                f"SSH tunnel to {SSH_HOST} failed (exit {self.proc.returncode}) — "
                "check connectivity, or re-run with --source docker-logs."
            )
        atexit.register(self.close)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._signal_close)

    def _signal_close(self, signum, frame) -> None:
        self.close()
        sys.exit(1)

    def close(self) -> None:
        subprocess.run(
            # "--" stops pkill (BSD/macOS) from parsing the "-L ..." pattern as its
            # own option flags — without it, close() silently fails to kill the tunnel.
            ["pkill", "-f", "--", f"-L {self.port}:localhost:3100"],
            capture_output=True,
            check=False,
        )


def _warn_if_capped(events: list[dict]) -> None:
    """Warn if a Loki query result was capped at 5000 entries."""
    if len(events) == 5000:
        print(
            "warning: Loki result capped at 5000 entries — the window may be truncated "
            "(missing older events). Narrow --since, or add --mode errors / --msg to "
            "filter server-side.",
            file=sys.stderr,
        )


def fetch_events(
    role: str,
    env: str,
    since: str,
    until: str = "now",
    min_level: str | None = None,
    msg_filter: list[str] | None = None,
) -> list[dict]:
    tunnel = _Tunnel()
    selector = build_selector(role, env, min_level=min_level, msg_filter=msg_filter)
    query = urllib.parse.urlencode(
        {
            "query": selector,
            "start": _to_unix_ns(since),
            "end": _to_unix_ns(until),
            "limit": 5000,
        }
    )
    url = f"http://localhost:{tunnel.port}/loki/api/v1/query_range?{query}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError) as e:
        raise LokiUnreachableError(
            f"Loki did not respond on the tunnel — falling back to docker-logs isn't "
            f"automatic; re-run with --source docker-logs, or check 'ssh ... docker ps' "
            f"on the host. ({e})"
        ) from e
    finally:
        tunnel.close()
    events = parse_query_range_response(data)
    _warn_if_capped(events)
    return events


def _to_unix_ns(spec: str) -> int:
    """Accept docker-style durations ('1h', '24h', '7d'), 'now', or an ISO timestamp."""
    if spec == "now":
        return int(time.time() * 1e9)
    if spec[-1] in ("s", "m", "h", "d") and spec[:-1].isdigit():
        n = int(spec[:-1])
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[spec[-1]]
        return int((time.time() - n * seconds) * 1e9)
    import datetime

    return int(datetime.datetime.fromisoformat(spec).timestamp() * 1e9)
