"""Write verbs for workspace collections: define (add/redefine) and delete.

Collections are a single Git-tracked file, ``.kajet/collections.yaml`` — no DB mirror
(see ``collections.py``). Writes go through ``GitRepository.transaction()`` (the
in-process lock plus cross-process flock) around a read-modify-write-commit sequence,
the same primitive ``api/workspaces/notes/crud/folders.py`` uses for its one-file
``.gitkeep`` marker — not ``staged_note_write``, which is built for multi-file note-body
batches with per-item rollback.
"""

import os
import tempfile
from pathlib import Path

from kajet_turbo.collections import (
    Cardinality,
    CollectionDefinition,
    _static_prefix,
    collides,
    dropped_members,
    dump_collections,
    load_collections,
    validate_definition,
)
from kajet_turbo.periods import PeriodKind
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import NoteRepository


def _write_collections_file(ws_path: str, definitions: dict[str, CollectionDefinition]) -> None:
    """Atomically overwrite ``.kajet/collections.yaml`` with ``definitions``' canonical
    form. Same tempfile-in-target-dir + fsync + ``Path.replace`` idiom as
    ``workspace.write_note_file`` — a reader never observes a partial write.
    """
    target = Path(ws_path, ".kajet", "collections.yaml")
    target.parent.mkdir(parents=True, exist_ok=True)
    text = dump_collections(definitions)
    fd, temp_path = tempfile.mkstemp(dir=target.parent, prefix=".collections.", suffix=".tmp")
    temp = Path(temp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def _serialize(definition: CollectionDefinition) -> dict:
    return {
        "name": definition.name,
        "grain": definition.grain,
        "cardinality": definition.cardinality,
        "folder": definition.folder,
        "title": definition.title,
        "description": definition.description,
    }


class CollectionService:
    def __init__(self, note_repo: NoteRepository):
        self._note_repo = note_repo

    def list_collections(self, ws_path: str) -> dict[str, CollectionDefinition]:
        return load_collections(ws_path)

    def _notes_under(self, ws_name: str, owner_id: str, folder: str) -> list[tuple[str, str]]:
        """(folder, title) pairs of every note that could possibly be a member of a
        collection whose folder template has ``folder`` as its static prefix. An empty
        prefix means the template's first segment is already a placeholder, so no
        folder scoping is possible — every note in the workspace is a candidate.
        """
        if folder:
            notes = self._note_repo.list_under_folder(ws_name, owner_id, folder)
            return [(n.folder, n.title) for n in notes]
        return [(n.folder, n.title) for n in self._note_repo.list_paths(ws_name, owner_id)]

    def define_collection(
        self,
        ws_path: str,
        ws_name: str,
        owner_id: str,
        name: str,
        grain: PeriodKind,
        cardinality: Cardinality,
        folder: str,
        title: str,
        description: str | None = None,
        *,
        dry_run: bool = False,
    ) -> dict:
        """Add a new collection, or redefine an existing one by name.

        Redefinition never moves or touches note files: changing a pattern only
        changes which notes count as members going forward — existing notes stay
        exactly where they are (see ``collides``/``dropped_members`` for why). This
        reports how many notes drop out of membership as a result (``affected_count``,
        ``dropped``), it never refuses or applies the redefinition for the caller's
        sake. Set ``dry_run=True`` to see that count without writing anything.

        Raises ``ValueError`` if the pattern is invalid, or if it collides with
        another collection's folder pattern (same name is not a collision with
        itself — that is exactly what a redefinition is).
        """
        existing = load_collections(ws_path)
        candidate = validate_definition(name, grain, cardinality, folder, title, description)
        for other_name, other in existing.items():
            if other_name != candidate.name and collides(candidate, other):
                raise ValueError(
                    f"Collection '{candidate.name}' would collide with '{other_name}': "
                    "both folder patterns can render the same path."
                )
        old = existing.get(candidate.name)
        dropped: list[tuple[str, str]] = []
        if old is not None:
            prefix = _static_prefix(old.folder)
            pairs = self._notes_under(ws_name, owner_id, prefix)
            dropped = dropped_members(old, candidate, pairs)
        verb = "add" if old is None else "update"
        dropped_payload = [{"folder": f, "title": t} for f, t in dropped]
        if dry_run:
            return {
                "name": candidate.name,
                "verb": verb,
                "would_write": True,
                "affected_count": len(dropped),
                "dropped": dropped_payload,
            }
        updated = {**existing, candidate.name: candidate}
        repo = GitRepository(ws_path)
        with repo.transaction():
            _write_collections_file(ws_path, updated)
            repo.commit_file(".kajet/collections.yaml", f"collections: {verb} {candidate.name}")
        return {
            "name": candidate.name,
            "verb": verb,
            "affected_count": len(dropped),
            "dropped": dropped_payload,
            "collection": _serialize(candidate),
        }

    def delete_collection(self, ws_path: str, name: str) -> dict:
        """Remove a collection definition. Non-destructive by construction: this only
        edits ``.kajet/collections.yaml`` — its former entries simply become loose
        notes, no note file is ever touched or moved. Do not "fix" this into a
        file-moving operation; that is the deliberate policy, not an oversight.
        """
        existing = load_collections(ws_path)
        if name not in existing:
            raise ValueError(f"Collection '{name}' does not exist.")
        updated = {n: d for n, d in existing.items() if n != name}
        repo = GitRepository(ws_path)
        with repo.transaction():
            _write_collections_file(ws_path, updated)
            repo.commit_file(".kajet/collections.yaml", f"collections: delete {name}")
        return {"name": name, "deleted": True}
