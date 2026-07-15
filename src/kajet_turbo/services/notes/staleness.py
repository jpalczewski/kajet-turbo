"""Expected-sha staleness checks shared by note write paths.

expected_sha proves the caller has read the version it is about to mutate.
``None`` at a service boundary means a trusted human-driven caller (REST API)
and skips the check at the call site. A mismatch never reveals the current
sha, forcing a real re-read instead of a blind retry.
"""

from kajet_turbo.repositories.git import GitRepository


def current_head_sha(ws_path: str, relative: str) -> str | None:
    history = GitRepository(ws_path).file_history(relative, limit=1)
    return history[0]["sha"] if history else None


def sha_is_fresh(current_sha: str | None, expected_sha: str) -> bool:
    expected = expected_sha.strip()
    return bool(expected) and current_sha is not None and current_sha.startswith(expected)


def stale_error(note_id: str) -> str:
    return (
        f"expected_sha nieaktualny dla {note_id}. Wywołaj get_note, "
        "by pobrać aktualną treść przed ponowną edycją."
    )


def stale_payload(note_id: str) -> dict:
    return {"note_id": note_id, "stale_sha": True, "error": stale_error(note_id)}
