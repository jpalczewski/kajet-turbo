"""Expected-sha staleness checks shared by note write paths.

expected_sha proves the caller has read the version it is about to mutate.
Callers that accept ``expected_sha: str | None`` treat ``None`` as a trusted
human-driven caller (REST API) and skip the check before calling into this
module. A mismatch never reveals the current sha, forcing a real re-read
instead of a blind retry.
"""

from kajet_turbo.repositories.git import GitRepository


def current_head_sha(
    ws_path: str, relative: str, git_repo: GitRepository | None = None
) -> str | None:
    """``git_repo`` lets batch callers reuse one open repo across N checks
    instead of re-opening the workspace per item; ``None`` opens it here."""
    repo = git_repo if git_repo is not None else GitRepository(ws_path)
    history = repo.file_history(relative, limit=1)
    return history[0]["sha"] if history else None


def sha_is_fresh(current_sha: str | None, expected_sha: str | None) -> bool:
    expected = (expected_sha or "").strip()
    return bool(expected) and current_sha is not None and current_sha.startswith(expected)


def stale_error(note_id: str) -> str:
    return (
        f"expected_sha nieaktualny dla {note_id}. Wywołaj get_note, "
        "by pobrać aktualną treść przed ponowną edycją."
    )


def stale_payload(note_id: str) -> dict:
    return {"note_id": note_id, "stale_sha": True, "error": stale_error(note_id)}
