"""Write verbs for workspace collections: define (add/redefine), delete, and open_entry.

Collections are a single Git-tracked file, ``.kajet/collections.yaml`` — no DB mirror
(see ``collections.py``). ``define_collection``/``delete_collection`` go through
``GitRepository.transaction()`` (the in-process lock plus cross-process flock) around a
read-modify-write-commit sequence, the same primitive ``api/workspaces/notes/crud/folders.py``
uses for its one-file ``.gitkeep`` marker — not ``staged_workspace_change``, which is built
for multi-file note-body batches with per-item rollback. ``open_entry`` writes a note body
instead of the collections file, so it delegates to ``NoteService.save`` (which does use
``staged_workspace_change``) under the same reentrant workspace lock.
"""

import os
import stat
import tempfile
from dataclasses import asdict
from datetime import date
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
from kajet_turbo.log import logger
from kajet_turbo.periods import Period, PeriodKind
from kajet_turbo.repositories.git import GitRepository, workspace_write_transaction
from kajet_turbo.repositories.notes import NoteRepository, note_to_list_item
from kajet_turbo.services.notes.service import NoteService


def _write_collections_file(ws_path: str, definitions: dict[str, CollectionDefinition]) -> None:
    """Atomically overwrite ``.kajet/collections.yaml`` with ``definitions``' canonical
    form. Same tempfile-in-target-dir + fsync + ``Path.replace`` idiom as
    ``workspace.write_note_file`` — a reader never observes a partial write — including
    that function's mode preservation: ``mkstemp`` always creates its temp file 0600,
    so without an explicit ``fchmod`` back to the target's existing mode (or 0644 for a
    brand-new file), every write here would silently narrow collections.yaml to
    owner-only permissions.
    """
    target = Path(ws_path, ".kajet", "collections.yaml")
    target.parent.mkdir(parents=True, exist_ok=True)
    text = dump_collections(definitions)
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, temp_path = tempfile.mkstemp(dir=target.parent, prefix=".collections.", suffix=".tmp")
    temp = Path(temp_path)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def _serialize(definition: CollectionDefinition) -> dict:
    return asdict(definition)


def _temporal_for(grain: PeriodKind, when: date) -> tuple[date | None, str | None]:
    """The (occurred_at, period) frontmatter pair an entry addressed by ``when`` at
    ``grain`` should carry — day grain is a point in time, everything coarser is a
    period. Mutually exclusive by construction, matching NoteService.save's contract.
    """
    if grain == "day":
        return when, None
    return None, Period.containing(when, grain).key


class CollectionService:
    def __init__(self, note_repo: NoteRepository, note_service: NoteService):
        self._note_repo = note_repo
        self._note_service = note_service

    def list_collections(self, ws_path: str) -> dict[str, CollectionDefinition]:
        return load_collections(ws_path)

    def _require_definition(self, ws_path: str, name: str) -> CollectionDefinition:
        definition = load_collections(ws_path).get(name)
        if definition is None:
            raise ValueError(f"Collection '{name}' does not exist.")
        return definition

    def folder_prefix(self, ws_path: str, name: str) -> str | None:
        """The folder scope ``entries_in(collection=name)`` should search: ``name``'s
        static folder prefix (#112's "a collection name and a folder prefix select the
        same set" — loose, path-boundary scoping, not exact membership; that stricter
        check is ``list_entries``'s job, not this one). ``None`` means the template's
        first segment is itself a placeholder, so no folder scoping is possible and the
        caller must search the whole workspace, not just its root (an empty string
        would mean root-only to ``entries_in``, which is wrong here).
        """
        definition = self._require_definition(ws_path, name)
        return _static_prefix(definition.folder) or None

    def list_entries(self, ws_path: str, ws_name: str, owner_id: str, name: str) -> list[dict]:
        """Every note currently a member of collection ``name``, across its whole
        history — not just a recent window. Membership is exact
        (``CollectionDefinition.matches``), not the bounded ``render_set`` sampling
        ``define_collection``'s collision/redefinition-impact checks use: those only
        need "would this plausibly collide", this needs "is this actually a member",
        and a workspace can hold entries far outside any reasonable sampling window. A
        note that merely lives under the collection's folder without a matching title
        does not count — unlike ``folder_prefix`` (used when a period already narrows
        the search), this has no period to lean on, so it needs the stricter check to
        stay meaningful.
        """
        definition = self._require_definition(ws_path, name)
        prefix = _static_prefix(definition.folder)
        if prefix:
            candidates = self._note_repo.list_under_folder(ws_name, owner_id, prefix)
            notes = [note_to_list_item(n) for n in candidates]
        else:
            notes = self._note_repo.list_notes(ws_name, owner_id, folder=None, limit=None)
        matched = [n for n in notes if definition.matches(n["folder"], n["title"])]
        matched.sort(key=lambda n: (n["folder"], n["title"]))
        logger.info("collection_entries_listed", ws=ws_name, collection=name, count=len(matched))
        return matched

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

        The read of the current ``collections.yaml``, the collision check, and (for a
        write) the write+commit all happen inside the same ``repo.transaction()`` —
        not just the write. Reading ``existing`` before acquiring the lock would let two
        concurrent callers both build their ``updated`` mapping from the same stale
        snapshot; the second writer would then silently discard the first writer's
        change when it commits. ``dry_run`` reads under the lock too, so its report
        reflects a real, un-raced snapshot even though it writes nothing.
        """
        candidate = validate_definition(name, grain, cardinality, folder, title, description)
        repo = GitRepository(ws_path)
        with repo.transaction():
            existing = load_collections(ws_path)
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

        Reads ``collections.yaml`` under the same lock as the write, for the same reason
        ``define_collection`` does — see its docstring.
        """
        repo = GitRepository(ws_path)
        with repo.transaction():
            existing = load_collections(ws_path)
            if name not in existing:
                raise ValueError(f"Collection '{name}' does not exist.")
            updated = {n: d for n, d in existing.items() if n != name}
            _write_collections_file(ws_path, updated)
            repo.commit_file(".kajet/collections.yaml", f"collections: delete {name}")
        return {"name": name, "deleted": True}

    @workspace_write_transaction
    def open_entry(self, ws_path: str, ws_name: str, owner_id: str, name: str, when: date) -> dict:
        """Resolve or create ``name``'s entry addressed by ``when``.

        ``cardinality="one"``: idempotent — the period key is an address, so this always
        resolves to the same note, never a duplicate alongside it. ``cardinality="many"``:
        entries are logged, not addressed, so this always creates a new one and allocates
        the next ordinal (see ``_next_ordinal``).

        ``workspace_write_transaction`` makes "does it already exist" and "create it" one
        atomic step under the same reentrant, cross-process workspace lock ``NoteService.save``
        itself takes (``git.py:_workspace_lock``) — no other writer can land a colliding note
        between the check and the create, so the ``FileExistsError`` ``save`` can still raise
        stays a defensive backstop (a ghost DB row, a case-fold collision), not the normal
        control path for concurrent callers.

        Not in scope: templates. A collection without one creates an empty note in the
        right place with the right title, which is where the value is.
        """
        definition = self._require_definition(ws_path, name)

        ordinal: int | None = None
        payload: dict | None = None
        if definition.cardinality == "one":
            folder, title = definition.render(when)
            existing = self._note_repo.get_by_path(ws_name, owner_id, folder, title)
            if existing is not None:
                payload = {
                    "note_id": existing.id,
                    "folder": folder,
                    "title": title,
                    "created": False,
                    "ordinal": None,
                    "occurred_at": existing.occurred_at,
                    "period": existing.period,
                }
        else:
            ordinal = self._next_ordinal(ws_name, owner_id, definition, when)
            folder, title = definition.render(when, ordinal)

        if payload is None:
            occurred_at, period = _temporal_for(definition.grain, when)
            result = self._note_service.save(
                owner_id,
                ws_name,
                ws_path,
                title,
                "",
                [],
                folder=folder,
                occurred_at=occurred_at,
                period=period,
            )
            payload = {
                "note_id": result["note_id"],
                "folder": folder,
                "title": title,
                "created": True,
                "ordinal": ordinal,
                "occurred_at": result["occurred_at"],
                "period": result["period"],
            }

        logger.info(
            "collection_entry_opened",
            ws=ws_name,
            collection=name,
            grain=definition.grain,
            created=payload["created"],
            ordinal=ordinal,
        )
        return payload

    def _next_ordinal(
        self, ws_name: str, owner_id: str, definition: CollectionDefinition, when: date
    ) -> int:
        """The next ordinal for ``definition``'s entries on ``when``: the max ordinal
        already in use among today's siblings, plus one — never a reused or renumbered
        value (see ``CollectionDefinition.sibling_pattern``). Scoped to
        ``_static_prefix(definition.folder)`` the same way redefinition-impact queries
        are, since a folder template can itself carry ``{ordinal}``.
        """
        pattern = definition.sibling_pattern(when)
        prefix = _static_prefix(definition.folder)
        pairs = self._notes_under(ws_name, owner_id, prefix)
        max_ordinal = 0
        for folder, title in pairs:
            match = pattern.match(f"{folder}/{title}")
            if match is not None:
                matched_ordinal = max((int(g) for g in match.groups()), default=0)
                max_ordinal = max(max_ordinal, matched_ordinal)
        return max_ordinal + 1
