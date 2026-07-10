"""Builds the callable NoteIndexer uses to defer embedding to the job queue.

Not a post-commit hook (unlike push/heal enqueue) — embedding is keyed to indexing,
not to git commits. The per-note dedup_key makes a burst of edits to the same note
coalesce into one pending ``embed_note`` job via the queue's debounce; ``user_id``
makes the job visible in the owner's jobs API."""

from collections.abc import Callable

from kajet_turbo.log import logger
from kajet_turbo.repositories.jobs import JobRepository


def make_enqueue_embed(jobs: JobRepository) -> Callable[[str, str, str], None]:
    def enqueue(note_id: str, workspace: str, owner_id: str) -> None:
        jobs.enqueue(
            "embed_note",
            {"note_id": note_id, "workspace": workspace, "owner_id": owner_id},
            dedup_key=f"{owner_id}:{workspace}:{note_id}",
            user_id=owner_id,
        )
        logger.debug("embed_enqueued", note_id=note_id)

    return enqueue
