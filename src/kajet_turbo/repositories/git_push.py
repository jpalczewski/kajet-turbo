"""Push a workspace's current branch to an external git remote over SSH.

dulwich threads ``ssh_command`` per-call down to the SSH vendor, so concurrent
pushes (worker thread pool) each use their own key with no global state. The key
and a TOFU known_hosts file are passed via the ssh command; host keys are trusted
on first contact and pinned thereafter (``accept-new``).

ConnectTimeout/ServerAlive* bound a remote that accepts the TCP connection but
never completes the SSH handshake or stalls mid-transfer — otherwise the push
(and, transitively, the worker's ThreadPoolExecutor shutdown) hangs forever
(#274)."""

from dulwich import porcelain
from dulwich.repo import Repo

from kajet_turbo.repositories.git import GitError

DEFAULT_SSH_CONNECT_TIMEOUT = 15  # seconds
DEFAULT_SSH_KEEPALIVE_INTERVAL = 15  # seconds
DEFAULT_SSH_KEEPALIVE_COUNT_MAX = 3


def build_ssh_command(
    key_path: str,
    known_hosts_path: str,
    *,
    connect_timeout: int = DEFAULT_SSH_CONNECT_TIMEOUT,
    keepalive_interval: int = DEFAULT_SSH_KEEPALIVE_INTERVAL,
    keepalive_count_max: int = DEFAULT_SSH_KEEPALIVE_COUNT_MAX,
) -> str:
    # IdentitiesOnly=yes: offer only our key (ignore any agent). accept-new: TOFU.
    # BatchMode=yes: never fall back to an interactive password/passphrase prompt.
    return (
        f"ssh -i {key_path} -o IdentitiesOnly=yes -o BatchMode=yes "
        f"-o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={known_hosts_path} "
        f"-o ConnectTimeout={connect_timeout} "
        f"-o ServerAliveInterval={keepalive_interval} "
        f"-o ServerAliveCountMax={keepalive_count_max}"
    )


def current_branch(ws_path: str) -> bytes:
    head = Repo(ws_path).refs.read_ref(b"HEAD")  # ty: ignore[invalid-argument-type] - Literal[b"HEAD"] satisfies Ref (bytes)
    if head is None:
        raise GitError("workspace has no HEAD")
    return head[len(b"ref: ") :] if head.startswith(b"ref: ") else head


def push(
    ws_path: str,
    origin_url: str,
    key_path: str,
    known_hosts_path: str,
    *,
    connect_timeout: int = DEFAULT_SSH_CONNECT_TIMEOUT,
    keepalive_interval: int = DEFAULT_SSH_KEEPALIVE_INTERVAL,
    keepalive_count_max: int = DEFAULT_SSH_KEEPALIVE_COUNT_MAX,
) -> None:
    branch = current_branch(ws_path)
    try:
        result = porcelain.push(
            ws_path,
            origin_url,
            refspecs=[branch],
            ssh_command=build_ssh_command(
                key_path,
                known_hosts_path,
                connect_timeout=connect_timeout,
                keepalive_interval=keepalive_interval,
                keepalive_count_max=keepalive_count_max,
            ),
        )
    except GitError:
        raise
    except Exception as e:
        raise GitError(str(e)) from e
    # A non-fast-forward / rejected ref shows up as a non-None status message.
    rejected = {
        ref.decode(errors="replace"): msg for ref, msg in (result.ref_status or {}).items() if msg
    }
    if rejected:
        raise GitError(f"push rejected: {rejected}")
