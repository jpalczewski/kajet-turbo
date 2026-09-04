"""Batch-reindex job handler (kind ``reindex_note``): file → chunks + FTS, then chains
into ``embed_note`` (stored chunks → vectors) in the same commit.

Replaces the request-path chunking that used to run inline inside ``NoteIndexer.index_many``
for batch/side-effect writes (``save_notes``, ``edit_notes``, ``rename_tag``,
``reindex_workspace``, backlink rewrites). ``index_note`` (single-note, synchronous) is
unaffected — it still chunks inline for read-your-writes.

Missing note or missing file is a terminal no-op: no exception, so the worker marks the job
``completed`` and never retries or resurrects a deleted note. A superseded generation (the
note changed again after this job was enqueued) is likewise a no-op — the newer edit's own
follow-up job will repair it.
"""

from collections.abc import Callable
from pathlib import Path

from kajet_turbo.embedding.base import EmbedderConfig
from kajet_turbo.log import logger
from kajet_turbo.markdown import chunk_markdown
from kajet_turbo.perf import incr, timed
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.notes import NoteChunkRepository, NoteRepository
from kajet_turbo.services.indexing import safe_resolve_backend
from kajet_turbo.workspace import note_filepath, read_note_file, workspace_path


class ReindexNoteHandler:
    def __init__(
        self,
        note_repo: NoteRepository,
        chunk_repo: NoteChunkRepository,
        jobs: JobRepository,
        resolve_cfg: Callable[[str], EmbedderConfig | None],
        workspaces_dir: str,
    ) -> None:
        self._notes = note_repo
        self._chunks = chunk_repo
        self._jobs = jobs
        self._resolve_cfg = resolve_cfg
        self._workspaces_dir = workspaces_dir

    def _skip(self, note_id: str, reason: str) -> None:
        incr("reindex_note_skipped")
        logger.info("reindex_note_skipped", note_id=note_id, reason=reason)

    def __call__(self, payload: dict) -> None:
        note_id = payload["note_id"]
        workspace = payload["workspace"]
        owner_id = payload["owner_id"]

        note = self._notes.get(note_id, owner_id=owner_id)
        if note is None:
            self._skip(note_id, "note_missing")
            return

        root = workspace_path(workspace, self._workspaces_dir, user_id=owner_id)
        path = note_filepath(root, note.folder, note.title)
        if not Path(path).is_file():
            self._skip(note_id, "file_missing")
            return

        _, content = read_note_file(path)
        with timed("chunk_ms"):
            chunks = chunk_markdown(content, title=note.title)

        with self._chunks.operation(
            "reindex_note", note_id=note_id, workspace=workspace, owner_id=owner_id
        ) as operation:
            session = operation.session
            applied = NoteChunkRepository.replace_chunks_in_session(
                session,
                note_id,
                workspace,
                owner_id,
                note.title,
                chunks,
                None,
                None,
                expected_generation=note.index_generation,
            )
            if not applied:
                session.rollback()
                operation.outcome = "superseded"
                incr("reindex_note_superseded")
                logger.info("reindex_note_superseded", note_id=note_id)
                return
            if chunks:
                incr("chunks", len(chunks))
                if safe_resolve_backend(self._resolve_cfg, owner_id) is not None:
                    self._jobs.enqueue_in_session(
                        session,
                        "embed_note",
                        {"note_id": note_id, "workspace": workspace, "owner_id": owner_id},
                        dedup_key=f"{owner_id}:{workspace}:{note_id}",
                        user_id=owner_id,
                    )
            session.commit()
