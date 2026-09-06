"""Query kajet-turbo logs from Loki (loopback-only port 3100, reached over an SSH tunnel).

Used by analyze-logs.py as the default event source.

The tunnel's destination is configuration, never a literal in this file:
KAJET_LOG_SSH_HOST, KAJET_LOG_SSH_USER, and KAJET_LOG_SSH_KEY (path to the private key).
Each is read from the process environment, falling back to a gitignored `.env` beside
this repository — or to the file KAJET_LOG_ENV_FILE points at. All three are required; a
missing one fails with a message naming it, before any connection is attempted.
--source docker-logs needs none of them.
"""

from __future__ import annotations

import atexit
import datetime
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

_SSH_ENV = {
    "host": "KAJET_LOG_SSH_HOST",
    "user": "KAJET_LOG_SSH_USER",
    "key": "KAJET_LOG_SSH_KEY",
}

_ENV_FILE_VAR = "KAJET_LOG_ENV_FILE"
# Resolved from this file, not the working directory: analyze-logs.py is run from
# wherever the operator happens to be standing.
_DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

LEVEL_ORDER = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}

# How far behind wall-clock the newest event can lag before we warn that the
# selector may not match what's currently being ingested (e.g. a relabeling
# change upstream), rather than a real outage.
_STALE_THRESHOLD_S = 120

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
    """Build a LogQL query for kajet-turbo logs.

    `service` (stable, e.g. "kajet-mcp") and `level` are Loki labels (see the
    Alloy pipeline's discovery.relabel + stage.labels config in the
    infrastructure repo) and go in the stream selector. `container` used to be a
    label too, but it embeds a redeploy-unique suffix and got dropped for
    cardinality — `service` replaces it. `msg` was dropped as a label for the
    same reason (it exploded per-UUID messages into their own streams), so it is
    filtered as a `| json` line filter instead of a label match. Everything else
    (--grep, --fields) stays client-side in analyze-logs.py, same as today.
    """
    parts = [
        'coolify_projectName="kajet-turbo"',
        f'service="kajet-{role}"',
        f'coolify_environmentName="{_normalize_env(env)}"',
    ]
    if min_level:
        # default-if-unrecognized matches mode_errors()'s own LEVEL_ORDER.get(min_level, 2)
        threshold = LEVEL_ORDER.get(min_level, 2)
        levels = [lvl for lvl, order in LEVEL_ORDER.items() if order >= threshold]
        parts.append(f'level=~"{"|".join(levels)}"')
    query = "{" + ", ".join(parts) + "}"
    if msg_filter:
        query += f' | json | msg=~"{"|".join(msg_filter)}"'
    return query


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


class LokiError(RuntimeError):
    """Base for errors whose message is remediation text the CLI prints verbatim."""


class LokiUnreachableError(LokiError):
    pass


class LokiConfigError(LokiError):
    pass


def _env_file_path() -> Path:
    override = os.environ.get(_ENV_FILE_VAR, "").strip()
    return Path(override).expanduser() if override else _DEFAULT_ENV_FILE


def _load_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` pairs from a dotenv-style file; missing file means no pairs.

    Deliberately minimal: comments, blank lines, an optional ``export`` prefix, and one
    layer of surrounding quotes. No interpolation, no multi-line values, no export
    semantics — anything richer belongs in a real settings library, and this file holds
    three connection strings.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError, NotADirectoryError, IsADirectoryError, PermissionError:
        return {}
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip().removeprefix("export ").lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


@dataclass(frozen=True, slots=True)
class SshTarget:
    """Where the tunnel connects, sourced from configuration rather than source.

    Field names map to environment variables through ``_SSH_ENV``; keeping that mapping
    in one place is what lets ``from_env`` read both sources and report every missing
    variable at once instead of one per failed run.
    """

    host: str
    user: str
    key: str

    @classmethod
    def from_env(cls) -> SshTarget:
        """The process environment wins over the file, so a one-off override is a
        prefixed variable rather than an edit to a file that outlives the run."""
        env_file = _env_file_path()
        from_file = _load_env_file(env_file)
        values: dict[str, str] = {}
        missing: list[str] = []
        for field in fields(cls):
            var = _SSH_ENV[field.name]
            value = (os.environ.get(var) or from_file.get(var, "")).strip()
            if value:
                values[field.name] = value
            else:
                missing.append(var)
        if missing:
            raise LokiConfigError(
                f"Loki access is not configured: {', '.join(missing)} "
                f"{'is' if len(missing) == 1 else 'are'} unset in the environment and in "
                f"{env_file}. Set {'it' if len(missing) == 1 else 'them'} to the log host, "
                "the SSH user, and the private key path, or re-run with "
                "--source docker-logs."
            )
        return cls(**values)


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class _Tunnel:
    def __init__(self, target: SshTarget) -> None:
        self.port = _free_local_port()
        self.proc = subprocess.Popen(
            [
                "ssh",
                "-f",
                "-N",
                "-L",
                f"{self.port}:localhost:3100",
                "-i",
                target.key,
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "StrictHostKeyChecking=accept-new",
                "-o",
                "ExitOnForwardFailure=yes",
                f"{target.user}@{target.host}",
            ],
        )
        self.proc.wait()  # -f backgrounds after auth; wait() reaps the launcher, not the tunnel
        if self.proc.returncode != 0:
            raise LokiUnreachableError(
                f"SSH tunnel to {target.host} failed (exit {self.proc.returncode}) — "
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


def _entry_timestamps(data: dict[str, Any]) -> list[int]:
    """Loki timestamps (ns) of every entry in a query_range response, oldest first.

    Counted before parsing on purpose: the 5000-entry cap and the freshness check are
    about what Loki returned, and a stream where most lines are not JSON (uvicorn
    tracebacks, for instance) would otherwise look uncapped and stale at once while the
    window had silently collapsed to a fraction of --since.
    """
    return sorted(
        int(ts_ns)
        for stream in data.get("data", {}).get("result", [])
        for ts_ns, _ in stream.get("values", [])
    )


def _warn_if_capped(entry_count: int) -> None:
    """Warn if a Loki query result was capped at 5000 entries (parsed or not)."""
    if entry_count == 5000:
        print(
            "warning: Loki result capped at 5000 entries — the window may be truncated "
            "(missing older events). Non-JSON lines count toward the cap too. Narrow "
            "--since, or add --mode errors / --msg to filter server-side.",
            file=sys.stderr,
        )


def _warn_if_stale(newest_ns: int | None, until: str) -> None:
    """Warn if the newest entry lags wall-clock 'now' by more than the threshold.

    A large lag usually means the selector no longer matches what's being
    ingested (e.g. an upstream relabeling change dropped or renamed a label
    this query still filters on) rather than a real outage — but either way
    the caller shouldn't trust the window on faith.
    """
    if until != "now" or newest_ns is None:
        return
    lag_s = time.time() - newest_ns / 1e9
    if lag_s > _STALE_THRESHOLD_S:
        print(
            f"warning: newest Loki event is {lag_s:.0f}s old — the selector may not match "
            "what's currently being ingested (relabeling change upstream?), not necessarily "
            "a real outage. Verify with a broader query or --source docker-logs before "
            "trusting this window.",
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
    tunnel = _Tunnel(SshTarget.from_env())
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
    stamps = _entry_timestamps(data)
    _warn_if_capped(len(stamps))
    _warn_if_stale(stamps[-1] if stamps else None, until)
    return parse_query_range_response(data)


def _to_unix_ns(spec: str) -> int:
    """Accept docker-style durations ('1h', '24h', '7d'), 'now', or an ISO timestamp."""
    if spec == "now":
        return int(time.time() * 1e9)
    if spec[-1] in ("s", "m", "h", "d") and spec[:-1].isdigit():
        n = int(spec[:-1])
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[spec[-1]]
        return int((time.time() - n * seconds) * 1e9)

    return int(datetime.datetime.fromisoformat(spec).timestamp() * 1e9)
