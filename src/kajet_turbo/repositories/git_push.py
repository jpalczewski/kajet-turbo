"""Push a workspace's current branch to an external git remote over SSH.

dulwich threads ``ssh_command`` per-call down to the SSH vendor, so concurrent
pushes (worker thread pool) each use their own key with no global state. The key
and a TOFU known_hosts file are passed via the ssh command; host keys are trusted
on first contact and pinned thereafter (``accept-new``)."""

from dulwich import porcelain
from dulwich.repo import Repo

from kajet_turbo.repositories.git import GitError


def build_ssh_command(key_path: str, known_hosts_path: str) -> str:
    # IdentitiesOnly=yes: offer only our key (ignore any agent). accept-new: TOFU.
    return (
        f"ssh -i {key_path} -o IdentitiesOnly=yes "
        f"-o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={known_hosts_path}"
    )


def current_branch(ws_path: str) -> bytes:
    head = Repo(ws_path).refs.read_ref(b"HEAD")  # ty: ignore[invalid-argument-type] - Literal[b"HEAD"] satisfies Ref (bytes)
    if head is None:
        raise GitError("workspace has no HEAD")
    return head[len(b"ref: ") :] if head.startswith(b"ref: ") else head


def push(ws_path: str, origin_url: str, key_path: str, known_hosts_path: str) -> None:
    branch = current_branch(ws_path)
    try:
        result = porcelain.push(
            ws_path,
            origin_url,
            refspecs=[branch],
            ssh_command=build_ssh_command(key_path, known_hosts_path),
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
