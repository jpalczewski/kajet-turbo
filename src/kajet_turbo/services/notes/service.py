import json
import re
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from functools import partial
from itertools import chain
from pathlib import Path
from typing import cast

import frontmatter
from nanoid import generate
from sqlmodel import Session

from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.log import logger
from kajet_turbo.markdown import (
    BrokenWikilinkError,
    IndexedNote,
    LinkResolution,
    LinkResolver,
    apply_edit,
    build_outline,
    join_target,
    split_target,
)
from kajet_turbo.models import Note
from kajet_turbo.periods import month_of_week, parse_period_key
from kajet_turbo.repositories.git import (
    GitRepository,
    defer_workspace_postprocess,
    workspace_write_transaction,
)
from kajet_turbo.repositories.link_reconcile import LinkReconcileRepository
from kajet_turbo.repositories.notes import (
    NoteChunkRepository,
    NoteLinkRepository,
    NoteRepository,
    NoteTagRepository,
    folder_sort_key,
)
from kajet_turbo.services.notes.folders import NoteFolderService
from kajet_turbo.services.notes.history import NoteVersionService
from kajet_turbo.services.notes.links import NoteLinkService, wikilink_warnings
from kajet_turbo.services.notes.paths import note_path_conflict
from kajet_turbo.services.notes.search import NoteSearchService
from kajet_turbo.services.notes.staged_write import StagedWrite, staged_note_write
from kajet_turbo.services.notes.staleness import (
    current_head_sha,
    sha_is_fresh,
    stale_error,
    stale_payload,
)
from kajet_turbo.services.notes.tags import NoteTagService
from kajet_turbo.services.notes.types import NoteData
from kajet_turbo.workspace import (
    InvalidFolderError,
    LocatedNote,
    NoteFrontmatter,
    iter_note_paths,
    locate_note,
    normalize_folder,
    normalize_temporal_metadata,
    note_filepath,
    note_folder,
    parse_frontmatter,
    read_note_file,
    resolve_temporal_fields,
    write_note_file,
)


@dataclass(frozen=True, slots=True)
class _ValidatedDestructiveItem:
    """A batch item that passed the validation shared by edits and deletes."""

    index: int
    raw: dict
    note_id: str
    loc: LocatedNote


@dataclass(frozen=True, slots=True)
class _PreparedEdit:
    """A fully validated edit, ready for the atomic write phase."""

    index: int
    note_id: str
    loc: LocatedNote
    meta: NoteFrontmatter
    old_content: str
    old_tags: list[str]
    new_content: str
    new_tags: list[str]
    occurred_at: str | None
    period: str | None
    links: LinkResolution
    replaced: int | None


@dataclass(frozen=True, slots=True)
class _BatchValidationError:
    """A public-shaped validation error, kept typed while flowing through a batch."""

    index: int
    note_id: str
    error: str

    def as_dict(self) -> dict:
        return {"index": self.index, "note_id": self.note_id, "error": self.error}


@dataclass(frozen=True, slots=True)
class _PresentFile:
    """One on-disk file successfully parsed during a reconcile scan."""

    note_id: str
    title: str
    tags: list[str]
    created_at: str
    updated_at: str
    occurred_at: str | None
    period: str | None
    content: str
    folder: str
    relative: str


def _present_file(
    note_id: str, meta: NoteFrontmatter, content: str, folder: str, relative: str
) -> _PresentFile:
    return _PresentFile(
        note_id=note_id,
        title=meta.title or "",
        tags=NoteTagService.normalize_tags(cast(list[str], meta.tags or [])),
        created_at=str(meta.created_at or ""),
        updated_at=str(meta.updated_at or ""),
        occurred_at=meta.occurred_at,
        period=meta.period,
        content=content,
        folder=folder,
        relative=relative,
    )


@dataclass(frozen=True, slots=True)
class _AdoptionCandidate:
    """An on-disk file with no ``id`` in frontmatter, found during a reconcile scan."""

    relative: str
    filepath: Path
    meta: NoteFrontmatter
    content: str


def _restore_bytes(path: Path, data: bytes) -> None:
    """``Path.write_bytes`` returns the byte count; ``StagedWrite.restore`` wants ``None``."""
    path.write_bytes(data)


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """Outcome of reconciling DB rows against a set of workspace paths."""

    inserted: list[str]
    updated: list[str]
    removed: list[str]
    unchanged: int
    duplicate_ids: list[str]
    unreadable_paths: list[str]
    adopted: list[str]

    @property
    def present(self) -> int:
        return len(self.inserted) + len(self.updated) + self.unchanged


# A reconcile refuses to execute a deletion this large rather than risk emptying the
# workspace from a path-computation bug — see reconcile_paths. Small workspaces losing a
# handful of notes to a legitimate cleanup never trip this (the floor guards that).
_RECONCILE_MAX_DELETE_RATIO = 0.2
_RECONCILE_MIN_DELETE_FLOOR = 5
_UNCHANGED = object()
_TEMPORAL_TOKEN = re.compile(
    r"(?<![0-9A-Za-z-])(?:\d{4}-\d{2}-\d{2}|\d{4}-W\d{2}|\d{4}-\d{2}|\d{4})(?![0-9A-Za-z-])"
)


def _folder_conflicts_with_period(folder: str, period) -> bool:
    """Treat calendar-looking folder components only as corroboration, never a source."""
    parts = folder.split("/")
    years = {part for part in parts if re.fullmatch(r"\d{4}", part)}
    if years and period.key[:4] not in years:
        return True
    if period.kind == "year":
        return False
    months = {part for part in parts if re.fullmatch(r"\d{2}", part)}
    if not months:
        return False
    # A week has no month of its own (periods.py); month_of_week's naming convention
    # is the same one collections.py uses to place weekly notes, so corroboration
    # against a folder's {month} component has to go through it too.
    month_key = month_of_week(period).key[5:7] if period.kind == "week" else period.key[5:7]
    return month_key not in months


def _classify_temporal_note(
    note_id: str, title: str, folder: str, occurred_at: str | None, period_value: str | None
) -> tuple[str, dict]:
    """Classify one note for temporal backfill.

    Returns ``(kind, payload)`` where ``kind`` is ``"candidate"``, ``"ambiguous"``, or
    ``"skipped"``. ``payload`` never carries a ``sha`` — callers attach it once they've
    resolved it (batched for a full-workspace preview, or already known for a specific
    note being re-validated at apply time).
    """
    if occurred_at is not None or period_value is not None:
        return "skipped", {"note_id": note_id, "reason": "already has temporal metadata"}
    matches = _TEMPORAL_TOKEN.findall(title)
    if len(matches) != 1:
        if matches:
            return "ambiguous", {
                "note_id": note_id,
                "title": title,
                "folder": folder,
                "reason": "title contains multiple temporal values",
            }
        return "skipped", {"note_id": note_id, "reason": "title has no canonical temporal value"}
    try:
        period = parse_period_key(matches[0])
    except ValueError:
        return "skipped", {"note_id": note_id, "reason": "title temporal value is invalid"}
    if _folder_conflicts_with_period(folder, period):
        return "ambiguous", {
            "note_id": note_id,
            "title": title,
            "folder": folder,
            "reason": "folder date conflicts with title",
        }
    field = "occurred_at" if period.kind == "day" else "period"
    return "candidate", {
        "note_id": note_id,
        "title": title,
        "folder": folder,
        "field": field,
        "value": period.key,
    }


class NoteService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        link_repo: NoteLinkRepository,
        tag_repo: NoteTagRepository,
        chunk_repo: NoteChunkRepository,
        tag_service: NoteTagService,
        link_service: NoteLinkService,
        search_service: NoteSearchService,
        version_service: NoteVersionService,
        folder_service: NoteFolderService,
        indexer=None,
        cache: WorkspaceCache | None = None,
        reconcile_repo: LinkReconcileRepository | None = None,
    ) -> None:
        self._crud_repo = crud_repo
        self._link_repo = link_repo
        self._tag_repo = tag_repo
        self._chunk_repo = chunk_repo
        self._tag_service = tag_service
        self._link_service = link_service
        self._search_service = search_service
        self._version_service = version_service
        self._folder_service = folder_service
        self._indexer = indexer
        self._cache = cache
        self._reconcile_repo = reconcile_repo

    def _locate(self, note_id: str, owner_id: str, ws_path: str) -> LocatedNote | None:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        return locate_note(note, ws_path)

    def _locate_batch(
        self, note_ids: list[str], owner_id: str, ws_path: str, git_repo: GitRepository
    ) -> dict[str, LocatedNote]:
        """Resolve every note in a batch and compute all head shas in ONE git walk,
        before per-item validation. One DB query for all rows, one git walk for all
        head shas, instead of N of each. Only paths that exist on disk enter the
        walk — a nonexistent file has no sha to resolve and would force a
        full-history walk with no early exit.

        DB rows and Path.exists() are read once here instead of per-item inside the
        validation loop, which shifts the TOCTOU window slightly relative to a
        concurrent write landing mid-batch. This is a conscious, acceptable change:
        the sha-freshness check was already advisory outside the workspace lock
        (a commit only takes the lock in commit_files/delete_files), so the class of
        guarantee callers get is unchanged — only the window's exact size shifts.
        """
        stripped_ids = [n for raw_id in note_ids if (n := raw_id.strip())]
        notes = self._crud_repo.get_many(stripped_ids, owner_id)
        located = {note.id: locate_note(note, ws_path) for note in notes}
        head_shas = git_repo.head_shas_for_paths(
            [loc.relative for loc in located.values() if loc.file_exists]
        )
        return {
            note_id: replace(loc, head_sha=head_shas.get(loc.relative))
            for note_id, loc in located.items()
        }

    def _validate_destructive_items(
        self,
        raw_items: list[dict],
        note_ids: list[str],
        located: dict[str, LocatedNote],
    ) -> Iterator[_ValidatedDestructiveItem | _BatchValidationError]:
        """Yield shared validation results in input order for batch writes.

        Keeping this as a stream lets edit_many add its edit-specific validation
        immediately, preserving the existing order of mixed validation errors.
        """
        seen_ids: set[str] = set()
        for index, (raw, note_id) in enumerate(zip(raw_items, note_ids, strict=True)):
            if not note_id:
                yield _BatchValidationError(index, note_id, "note_id jest wymagany.")
                continue
            if note_id in seen_ids:
                yield _BatchValidationError(
                    index, note_id, f"Duplikat note_id w batchu: '{note_id}'."
                )
                continue
            seen_ids.add(note_id)
            loc = located.get(note_id)
            if loc is None:
                yield _BatchValidationError(index, note_id, f"Notatka {note_id} nie znaleziona.")
                continue
            if not loc.file_exists:
                yield _BatchValidationError(
                    index, note_id, f"Plik notatki {note_id} nie znaleziony."
                )
                continue
            expected_sha = str(raw.get("expected_sha", "")).strip()
            if not expected_sha:
                yield _BatchValidationError(index, note_id, "expected_sha jest wymagany.")
                continue
            if not sha_is_fresh(loc.head_sha, expected_sha):
                yield _BatchValidationError(index, note_id, stale_error(note_id))
                continue
            yield _ValidatedDestructiveItem(index, raw, note_id, loc)

    def _note_data(self, loc: LocatedNote, sha: str | None) -> NoteData:
        if sha is None:
            raise ValueError(
                f"Notatka {loc.note.id} nie ma historii commitów (niespójny stan repo)."
            )
        _, content = read_note_file(loc.filepath)
        return NoteData(
            note_id=loc.note.id,
            workspace=loc.note.workspace,
            owner_id=loc.note.owner_id,
            title=loc.note.title,
            folder=loc.note.folder,
            tags=json.loads(loc.note.tags or "[]"),
            created_at=loc.note.created_at,
            updated_at=loc.note.updated_at,
            occurred_at=loc.note.occurred_at,
            period=loc.note.period,
            content=content,
            sha=sha,
        )

    def _index(
        self,
        note_id: str,
        ws_name: str,
        owner_id: str,
        title: str,
        content: str,
        expected_generation: int,
    ) -> None:
        # Chunks + FTS are the reliable search backbone (written by replace_chunks inside
        # index_note); a real DB write error surfaces. The embedding HTTP roundtrip is
        # deferred to an embed_note job — index_note only enqueues, never hits the network.
        if self._indexer is None:
            return
        self._indexer.index_note(
            note_id,
            ws_name,
            owner_id,
            title,
            content,
            expected_generation=expected_generation,
        )

    def _teardown_note(self, session: Session, note: Note) -> None:
        """Remove every DB artifact of ``note`` inside the caller's transaction."""
        self._tag_repo.delete_note_tags_in_session(session, note.id, note.workspace, note.owner_id)
        self._chunk_repo.delete_chunks(note.id, session)
        self._crud_repo.delete_in_session(session, note.id, owner_id=note.owner_id)
        self._link_repo.delete_links_from_in_session(session, note.id)
        self._link_repo.delete_links_to_in_session(session, note.id)
        self._link_service.delete_dangling_for_source_in_session(session, note.id)

    @workspace_write_transaction
    def save(
        self,
        user_id: str,
        ws_name: str,
        ws_path: str,
        title: str,
        content: str,
        tags: list[str],
        folder: str = "",
        occurred_at: object = None,
        period: object = None,
    ) -> dict:
        folder = normalize_folder(folder)
        occurred_at, period = normalize_temporal_metadata(occurred_at, period)
        tags = NoteTagService.normalize_tags(tags)
        workspace_links = self._link_service.for_workspace(ws_name, user_id)
        filepath = note_filepath(ws_path, folder, title)
        relative = str(Path(filepath).relative_to(ws_path))
        conflict = note_path_conflict(workspace_links.paths, ws_path, folder, title)
        if conflict is not None:
            raise FileExistsError(
                f"'{title}' would be stored as '{Path(filepath).name}', already used by "
                f"note '{conflict.title}' in folder '{conflict.folder or 'root'}'."
            )
        if Path(filepath).exists():
            raise FileExistsError(f"File '{Path(filepath).name}' already exists on disk.")
        links = workspace_links.validate(content, folder)
        affected_sources = workspace_links.affected_sources({title})
        note_id = generate(size=7)
        now = datetime.now(UTC).isoformat()
        meta = NoteFrontmatter(
            id=note_id,
            title=title,
            tags=tags,
            created_at=now,
            updated_at=now,
            occurred_at=occurred_at,
            period=period,
        )
        item = StagedWrite(
            relative=relative,
            apply=partial(write_note_file, filepath, meta, content),
            restore=partial(Path(filepath).unlink, missing_ok=True),
        )
        with staged_note_write(GitRepository(ws_path), [item], f"note: add {title}"):
            pass
        self._crud_repo.insert(
            note_id, ws_name, user_id, title, tags, now, now, folder, occurred_at, period
        )
        self._link_service.persist(note_id, ws_name, user_id, links)
        self._tag_service.sync_tags(note_id, ws_name, user_id, tags, content)
        if self._cache is not None:
            self._cache.bump(ws_name, user_id)
        logger.info("note_saved", note_id=note_id, ws=ws_name, folder=folder)
        defer_workspace_postprocess(
            ws_path, partial(self._index, note_id, ws_name, user_id, title, content, 1)
        )
        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(user_id, ws_name, affected_sources)
        return {
            "note_id": note_id,
            "warnings": wikilink_warnings(links),
            "occurred_at": occurred_at,
            "period": period,
        }

    @workspace_write_transaction
    def save_many(
        self,
        user_id: str,
        ws_name: str,
        ws_path: str,
        notes: list[dict],
    ) -> list[dict]:
        """Create many notes in one batch: one git commit, one cache bump, embeddings
        parallelized across the indexer threadpool. Best-effort per note — invalid notes
        are reported and skipped. Each input dict: ``{title, content, tags=[], folder=""}``.
        Returns per-note ``{index, note_id}`` | ``{index, error}``, input order preserved.
        Raises GitError or OSError if a write or the batch commit fails (every file
        actually written is rolled back first).
        """
        results: list[dict | None] = [None] * len(notes)
        now = datetime.now(UTC).isoformat()

        # Phase 1: uniqueness + id assignment. Survivors get an id and join the batch's
        # link index so in-batch wikilinks resolve in Phase 2. `base_links` is a snapshot
        # of the workspace's DB rows, taken once up front under the workspace write lock
        # (Phase 2 extends it via with_extra instead of re-querying); batch_notes
        # accumulates alongside it so note_path_conflict sees both existing rows and
        # notes already accepted earlier in this same batch.
        base_links = self._link_service.for_workspace(ws_name, user_id)
        existing_paths = base_links.paths
        accepted: set[tuple[str, str]] = set()
        batch_notes: list[IndexedNote] = []
        survivors: list[dict] = []
        for index, raw in enumerate(notes):
            title = str(raw.get("title", "")).strip()
            if not title:
                results[index] = {"index": index, "error": "Tytuł jest wymagany."}
                continue
            folder = normalize_folder(str(raw.get("folder", "")))
            key = (folder, title)
            if key in accepted:
                results[index] = {
                    "index": index,
                    "error": f"Duplikat w batchu: '{title}' w folderze '{folder or 'root'}'.",
                }
                continue
            conflict = note_path_conflict(
                chain(existing_paths, batch_notes), ws_path, folder, title
            )
            if conflict is not None:
                results[index] = {
                    "index": index,
                    "error": (
                        f"'{title}' would be stored as the same file as note "
                        f"'{conflict.title}' in folder '{conflict.folder or 'root'}'."
                    ),
                }
                continue
            note_id = generate(size=7)
            filepath = note_filepath(ws_path, folder, title)
            relative = str(Path(filepath).relative_to(ws_path))
            if Path(filepath).exists():
                results[index] = {
                    "index": index,
                    "error": f"File '{Path(filepath).name}' already exists on disk.",
                }
                continue
            accepted.add(key)
            batch_notes.append(IndexedNote(note_id, folder, title))
            survivors.append(
                {
                    "index": index,
                    "note_id": note_id,
                    "title": title,
                    "content": str(raw.get("content", "")),
                    "tags": NoteTagService.normalize_tags(raw.get("tags", []) or []),
                    "folder": folder,
                    "filepath": filepath,
                    "relative": relative,
                    "occurred_at": None,
                    "period": None,
                }
            )
            try:
                survivors[-1]["occurred_at"], survivors[-1]["period"] = normalize_temporal_metadata(
                    raw.get("occurred_at"), raw.get("period")
                )
            except ValueError as e:
                results[index] = {"index": index, "error": str(e)}
                survivors.pop()
                accepted.remove(key)
                batch_notes.pop()

        # Phase 2: wikilink resolution against existing notes union the batch, sharing one
        # index. Non-cascading: the index is not rebuilt as notes are dropped, so a link to
        # a later-dropped note still resolves (worst case a harmless orphan edge).
        valid: list[dict] = []
        workspace_links = base_links.with_extra(batch_notes)
        for s in survivors:
            try:
                s["links"] = workspace_links.validate(s["content"], s["folder"])
            except BrokenWikilinkError as e:
                results[s["index"]] = {"index": s["index"], "error": str(e)}
                continue
            valid.append(s)

        if not valid:
            return [r for r in results if r is not None]

        affected_sources = workspace_links.affected_sources({str(s["title"]) for s in valid})

        # Phase 3: write files, then one commit (roll back files on failure).
        n = len(valid)
        items = [
            StagedWrite(
                relative=s["relative"],
                apply=partial(
                    write_note_file,
                    s["filepath"],
                    NoteFrontmatter(
                        id=s["note_id"],
                        title=s["title"],
                        tags=s["tags"],
                        created_at=now,
                        updated_at=now,
                        occurred_at=s["occurred_at"],
                        period=s["period"],
                    ),
                    s["content"],
                ),
                restore=partial(Path(s["filepath"]).unlink, missing_ok=True),
            )
            for s in valid
        ]
        with staged_note_write(
            GitRepository(ws_path), items, f"note: add {n} note{'' if n == 1 else 's'}"
        ):
            pass

        # Phase 4: DB insert + link graph + tags.
        for s in valid:
            self._crud_repo.insert(
                s["note_id"],
                ws_name,
                user_id,
                s["title"],
                s["tags"],
                now,
                now,
                s["folder"],
                s["occurred_at"],
                s["period"],
            )
        self._link_service.persist_many(
            ws_name,
            user_id,
            {str(s["note_id"]): s["links"] for s in valid},
        )
        for s in valid:
            self._tag_service.sync_tags(s["note_id"], ws_name, user_id, s["tags"], s["content"])

        if self._cache is not None:
            self._cache.bump(ws_name, user_id)

        # Phase 5: index after releasing the workspace write lock. It remains synchronous
        # from the caller's perspective, including the existing error behavior.
        if self._indexer is not None:
            index_payload = [
                {
                    "id": s["note_id"],
                    "title": s["title"],
                    "content": s["content"],
                    "index_generation": 1,
                }
                for s in valid
            ]
            defer_workspace_postprocess(
                ws_path, partial(self._indexer.index_many, ws_name, user_id, index_payload)
            )

        for s in valid:
            results[s["index"]] = {
                "index": s["index"],
                "note_id": s["note_id"],
                "warnings": wikilink_warnings(s["links"]),
            }
            logger.info("note_saved", note_id=s["note_id"], ws=ws_name, folder=s["folder"])

        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(user_id, ws_name, affected_sources)

        return [r for r in results if r is not None]

    def get(self, note_id: str, owner_id: str) -> dict | None:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        return {
            "note_id": note.id,
            "workspace": note.workspace,
            "owner_id": note.owner_id,
            "title": note.title,
            "folder": note.folder,
            "tags": json.loads(note.tags or "[]"),
            "created_at": note.created_at,
            "updated_at": note.updated_at,
            "occurred_at": note.occurred_at,
            "period": note.period,
        }

    def get_with_content(self, note_id: str, owner_id: str, ws_path: str) -> NoteData | None:
        """Single-note read. Batch reads go through ``get_many``/``_locate_batch``
        instead — a shared git walk beats N of these."""
        loc = self._locate(note_id, owner_id, ws_path)
        if loc is None or not loc.file_exists:
            return None
        return self._note_data(loc, current_head_sha(ws_path, loc.relative))

    def resolve_note_id(
        self, title: str, folder: str | None, owner_id: str, ws_name: str
    ) -> str | None:
        """Resolve a ``(folder, title)`` natural key to a note id; ``None`` when unknown.

        Path semantics are the wikilink ones (``LinkIndex``): ``folder`` is a path *suffix*,
        an exact full path wins, and omitting it searches the whole workspace. Reusing that
        resolver keeps one definition of what a path means in this notebook.

        Unlike a wikilink, an ambiguous hit raises instead of best-guessing with a warning —
        a caller asking for one note by name would otherwise act on the wrong one. And unlike
        a wikilink, this stays exact/case-sensitive (``allow_casefold=False``): a wikilink is
        free text that benefits from a forgiving fallback, but this is an explicit API call —
        a case-mismatched title should be a loud not-found, not a silent guess.
        """
        target = join_target(folder or "", title)
        index = self._link_service.for_workspace(ws_name, owner_id).index
        match = index.resolve_detailed(target, allow_casefold=False)
        if match is None:
            return None
        # An exact full-path hit beats the alternatives; anything else is a real ambiguity.
        # The folder half comes from split_target, which is what the ranker scored against —
        # but only a folder the caller actually supplied can make a hit exact: for a bare
        # title the target's folder is "", which a root-level note matches by accident.
        exact = folder is not None and match.chosen.folder == split_target(target)[0]
        if match.alternatives and not exact:
            candidates = ", ".join(
                f"{note.folder or 'root'} ({note.note_id})"
                for note in (match.chosen, *match.alternatives)
            )
            raise ValueError(
                f"Niejednoznaczne: '{target}' pasuje do {len(match.alternatives) + 1} notatek "
                f"— {candidates}. Podaj pełniejszy folder albo note_id."
            )
        # No title here: logs are shipped off-box and note titles are personal content.
        logger.info("note_resolved_by_title", note_id=match.chosen.note_id)
        return match.chosen.note_id

    def get_with_content_by_title(
        self, title: str, folder: str | None, owner_id: str, ws_name: str, ws_path: str
    ) -> NoteData | None:
        """Read a note addressed by its ``(folder, title)`` natural key. See
        ``resolve_note_id`` for the path semantics."""
        note_id = self.resolve_note_id(title, folder, owner_id, ws_name)
        if note_id is None:
            return None
        return self.get_with_content(note_id, owner_id, ws_path)

    def get_outline(self, note_id: str, owner_id: str, ws_path: str) -> dict | None:
        """Note structure (headings + section sizes) without content — for picking a
        target_heading before a surgical edit_note call without loading the full body."""
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        filepath = note_filepath(ws_path, note.folder, note.title)
        if not Path(filepath).exists():
            return None
        _, content = read_note_file(filepath)
        sections, preamble_chars, preamble_lines = build_outline(content)
        return {
            "note_id": note.id,
            "title": note.title,
            "folder": note.folder,
            "updated_at": note.updated_at,
            "total_chars": len(content),
            "total_lines": content.count("\n") + 1 if content else 0,
            "preamble_chars": preamble_chars,
            "preamble_lines": preamble_lines,
            "sections": [asdict(s) for s in sections],
        }

    def export_folder(
        self, ws_name: str, owner_id: str, ws_path: str, folder: str, max_chars: int = 80_000
    ) -> dict:
        """Concatenate a folder's subtree into one markdown document — for corpus-style
        reading (analyze N related notes as one document) instead of N separate get_note
        calls. Truncates at a note boundary once max_chars is exceeded; the first note is
        always included in full so a folder starting with one huge note never exports empty.
        """
        scope = normalize_folder(folder)
        notes = sorted(
            self._crud_repo.list_under_folder(ws_name, owner_id, scope), key=folder_sort_key
        )
        parts: list[str] = []
        omitted: list[dict] = []
        total_chars = 0
        truncated = False
        for index, note in enumerate(notes):
            filepath = note_filepath(ws_path, note.folder, note.title)
            if not Path(filepath).exists():
                continue
            _, content = read_note_file(filepath)
            heading_path = join_target(note.folder, note.title)
            tags = json.loads(note.tags or "[]")
            tag_line = f"_Tagi: {', '.join(tags)}_\n\n" if tags else ""
            section = f"# {heading_path}\n\n{tag_line}{content}".rstrip() + "\n"
            if index > 0 and not truncated and total_chars + len(section) > max_chars:
                truncated = True
            if truncated and index > 0:
                omitted.append({"note_id": note.id, "title": note.title, "chars": len(section)})
                continue
            parts.append(section)
            total_chars += len(section)
        markdown = "\n---\n\n".join(parts)
        logger.info(
            "folder_exported", ws=ws_name, folder=scope, note_count=len(parts), truncated=truncated
        )
        return {
            "markdown": markdown,
            "note_count": len(parts),
            "total_chars": len(markdown),
            "truncated": truncated,
            "omitted": omitted,
        }

    def get_many(self, note_ids: list[str], owner_id: str, ws_path: str) -> list[NoteData | dict]:
        """Read multiple notes in one call. Best-effort per id: a missing note becomes
        {"note_id": ..., "error": ...} instead of failing the whole call. Order-preserving.
        All head shas come from ONE shared git walk (head_shas_for_paths) instead of a
        per-note history walk — cost is the deepest note's history, not the sum."""
        git_repo = GitRepository(ws_path)
        located = self._locate_batch(note_ids, owner_id, ws_path, git_repo)
        results: list[NoteData | dict] = []
        for note_id in note_ids:
            loc = located.get(note_id.strip())
            if loc is None or not loc.file_exists:
                results.append({"note_id": note_id, "error": f"Notatka {note_id} nie znaleziona."})
            else:
                results.append(self._note_data(loc, loc.head_sha))
        return results

    def preview_chunks(self, note_id: str, owner_id: str, ws_path: str) -> dict | None:
        """Live chunk preview for a note (reads current file content; never stored rows)."""
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        data = self.get_with_content(note_id, owner_id, ws_path)
        if data is None:
            return None
        chunks = self._indexer.preview(note.title, data.content, owner_id) if self._indexer else []
        return {
            "note_id": note.id,
            "title": note.title,
            "index_state": note.index_state,
            "chunk_count": len(chunks),
            "chunks": chunks,
        }

    def grep(
        self,
        ws_name: str,
        ws_path: str,
        pattern: str,
        folder: str | None = None,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> dict:
        """Literal (non-semantic) substring search over raw note files, including
        frontmatter — for exact-text lookups (refactors, "is this word still anywhere")
        that search_notes' FTS/vector/metadata ranking cannot guarantee. Scoped to
        folder's subtree when given. Does not touch the DB; the workspace's files on
        disk are the source of truth for grep, same as reindex/reconcile_paths.
        """
        if not pattern.strip():
            raise ValueError("Wzorzec wyszukiwania nie może być pusty.")
        scope = normalize_folder(folder) if folder is not None else None
        ws_root = Path(ws_path)
        needle = pattern if case_sensitive else pattern.casefold()
        matches: list[dict] = []
        truncated = False
        for filepath in sorted(ws_root.rglob("*.md")):
            if ".git" in filepath.parts:
                continue
            folder = note_folder(ws_path, filepath)
            if scope is not None and not (folder == scope or folder.startswith(scope + "/")):
                continue
            raw = filepath.read_text(encoding="utf-8")
            meta, content = parse_frontmatter(frontmatter.loads(raw))
            note_id = str(meta.id or "")
            title = str(meta.title or "")
            # line_number is relative to the note body (what get_note returns as
            # `content`), since that's the only view an agent can act on — a raw
            # file line number would point at nothing in the API response. Offset
            # by the frontmatter block's line count; a match inside the frontmatter
            # itself (e.g. a tag) has no body line, so it reports 0.
            fm_offset = len(raw[: raw.rfind(content)].splitlines()) if content else 0
            for raw_line_number, line in enumerate(raw.splitlines(), start=1):
                haystack = line if case_sensitive else line.casefold()
                if needle not in haystack:
                    continue
                if len(matches) >= max_results:
                    truncated = True
                    break
                matches.append(
                    {
                        "note_id": note_id,
                        "title": title,
                        "folder": folder,
                        "line_number": max(0, raw_line_number - fm_offset),
                        "line": line,
                    }
                )
            if truncated:
                break
        logger.info("notes_grep", ws=ws_name, matches=len(matches), truncated=truncated)
        return {"matches": matches, "truncated": truncated}

    @workspace_write_transaction
    def update(
        self,
        note_id: str,
        owner_id: str,
        ws_path: str,
        expected_sha: str,
        title: str | None = None,
        content: str | None = None,
        tags: list[str] | None = None,
        folder: str | None = None,
        mode: str = "overwrite",
        target_heading: str | None = None,
        old_str: str | None = None,
        new_str: str | None = None,
        replace_all: bool = False,
        *,
        extras: dict[str, object] | None = None,
        occurred_at: object = _UNCHANGED,
        period: object = _UNCHANGED,
        clear_date_metadata: bool = False,
    ) -> dict:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        now = datetime.now(UTC).isoformat()
        new_title = title if title is not None else note.title
        try:
            new_folder = normalize_folder(folder) if folder is not None else note.folder
        except ValueError as e:
            raise InvalidFolderError(str(e)) from e
        current_tags = json.loads(note.tags or "[]")
        new_tags = NoteTagService.normalize_tags(tags) if tags is not None else current_tags
        new_occurred_at, new_period = resolve_temporal_fields(
            has_occurred_at=occurred_at is not _UNCHANGED,
            has_period=period is not _UNCHANGED,
            occurred_at=occurred_at,
            period=period,
            clear=clear_date_metadata,
            fallback=(note.occurred_at, note.period),
        )

        old_path = note_filepath(ws_path, note.folder, note.title)
        new_path = note_filepath(ws_path, new_folder, new_title)
        old_rel = str(Path(old_path).relative_to(ws_path))
        new_rel = str(Path(new_path).relative_to(ws_path))

        if not Path(old_path).exists():
            raise FileNotFoundError(f"Plik notatki {note_id} nie znaleziony.")

        if not sha_is_fresh(current_head_sha(ws_path, old_rel), expected_sha):
            return stale_payload(note_id)

        workspace_links = self._link_service.for_workspace(note.workspace, owner_id)
        if old_path != new_path:
            conflict = note_path_conflict(
                workspace_links.paths, ws_path, new_folder, new_title, exclude_id=note_id
            )
            if conflict is not None:
                raise FileExistsError(
                    f"'{new_title}' would be stored as '{Path(new_path).name}', already used by "
                    f"note '{conflict.title}' in folder '{conflict.folder or 'root'}'."
                )
            if Path(new_path).exists():
                raise FileExistsError(f"Target file '{new_rel}' already exists.")
        existing_meta, old_content = read_note_file(old_path)
        if not clear_date_metadata and occurred_at is _UNCHANGED and period is _UNCHANGED:
            # The file is source of truth during a read-modify-write. This also repairs
            # a temporal value hand-edited since the last reconcile instead of overwriting it.
            new_occurred_at, new_period = existing_meta.occurred_at, existing_meta.period
        new_extras = extras if extras is not None else existing_meta.extras
        # apply_edit owns every mode/parameter rule, including "overwrite without content
        # leaves the body alone" — the metadata-only edit path.
        edit_result = apply_edit(
            old_content,
            mode,
            content=content,
            old_str=old_str,
            new_str=new_str,
            target_heading=target_heading,
            replace_all=replace_all,
        )
        new_content = edit_result.body
        replaced = edit_result.replaced

        # workspace_links was hoisted above (before the collision check) — this snapshot
        # serves validation and any backlink rewrite below too.
        links = workspace_links.validate(new_content, new_folder)
        identity_changed = note.title != new_title or note.folder != new_folder
        affected_sources = (
            workspace_links.affected_sources({note.title, new_title}, include_source_ids={note_id})
            if identity_changed
            else set()
        )

        # A rename is its own commit, made outside the staged write below (#118 tracks
        # making rename+content atomic; a failure here leaves nothing to roll back since
        # the content write hasn't happened yet). One GitRepository for both: a second
        # open would re-read refs/pack indexes for no reason.
        repo = GitRepository(ws_path)
        if old_path != new_path:
            Path(new_path).parent.mkdir(parents=True, exist_ok=True)
            repo.rename_file(old_rel, new_rel, f"note: rename to {new_title}")
        apply_meta = replace(
            existing_meta,
            id=note_id,
            title=new_title,
            tags=new_tags,
            created_at=note.created_at,
            updated_at=now,
            extras=new_extras,
            occurred_at=new_occurred_at,
            period=new_period,
        )
        restore_meta = replace(
            existing_meta,
            id=note_id,
            title=note.title,
            tags=current_tags,
            created_at=note.created_at,
            updated_at=note.updated_at,
            occurred_at=note.occurred_at,
            period=note.period,
        )
        item = StagedWrite(
            relative=new_rel,
            apply=partial(write_note_file, new_path, apply_meta, new_content),
            restore=partial(write_note_file, new_path, restore_meta, old_content),
        )
        with staged_note_write(repo, [item], f"note: update {new_title}"):
            pass

        self._crud_repo.update(
            note_id,
            owner_id=owner_id,
            title=new_title,
            content=new_content,
            tags=new_tags,
            updated_at=now,
            folder=new_folder,
            occurred_at=new_occurred_at,
            period=new_period,
            bump_index_generation=True,
        )
        self._link_service.persist(note_id, note.workspace, owner_id, links)
        self._tag_service.sync_tags(note_id, note.workspace, owner_id, new_tags, new_content)
        if old_path != new_path:
            move = (
                IndexedNote(note_id, note.folder, note.title),
                IndexedNote(note_id, new_folder, new_title),
            )
            workspace_links.rewrite_backlinks([move], ws_path)
        if self._cache is not None:
            self._cache.bump(note.workspace, owner_id)
        logger.info("note_updated", note_id=note_id, folder=new_folder)
        defer_workspace_postprocess(
            ws_path,
            partial(
                self._index,
                note_id,
                note.workspace,
                owner_id,
                new_title,
                new_content,
                note.index_generation + 1,
            ),
        )
        if self._reconcile_repo is not None and identity_changed:
            self._reconcile_repo.mark_and_enqueue(owner_id, note.workspace, affected_sources)
        return {
            "note_id": note_id,
            "replaced": replaced,
            "warnings": wikilink_warnings(links),
            "occurred_at": new_occurred_at,
            "period": new_period,
        }

    @workspace_write_transaction
    def edit_many(
        self,
        user_id: str,
        ws_name: str,
        ws_path: str,
        edits: list[dict],
    ) -> dict:
        """Apply multiple surgical edits in ONE atomic commit. All-or-nothing at
        validation: any invalid edit (missing note, duplicate note_id, broken wikilink,
        bad anchor/heading) rejects the whole batch — nothing is written. Content + tags
        only; no title/folder changes (a rename needs backlink rewrites across other
        notes, incompatible with one commit_files call — use update() for that). Each
        input dict: {note_id, mode="append", content=None, target_heading=None,
        old_str=None, new_str=None, replace_all=False, tags=None}.
        """
        if not edits:
            raise ValueError("Batch edycji nie może być pusty.")

        # One open repo for the whole batch: staleness checks during validation and
        # the single atomic commit afterwards, instead of re-opening per item.
        git_repo = GitRepository(ws_path)
        note_ids = [str(raw.get("note_id", "")).strip() for raw in edits]
        located = self._locate_batch(note_ids, user_id, ws_path, git_repo)
        workspace_links = self._link_service.for_workspace(ws_name, user_id)
        errors: list[dict] = []
        prepared: list[_PreparedEdit] = []
        for item in self._validate_destructive_items(edits, note_ids, located):
            if isinstance(item, _BatchValidationError):
                errors.append(item.as_dict())
                continue
            index, raw, note_id, loc = item.index, item.raw, item.note_id, item.loc
            existing_meta, old_content = read_note_file(loc.filepath)
            # 'overwrite' without content is edit_note's metadata-only path, but this batch
            # cannot rename or move — so with no tags either, the item has nothing left to
            # change and would commit an untouched file while reporting success. Every other
            # mode already errors on a missing payload inside apply_edit.
            if (
                raw.get("mode", "append") == "overwrite"
                and raw.get("content") is None
                and raw.get("tags") is None
                and "occurred_at" not in raw
                and "period" not in raw
                and not raw.get("clear_date_metadata", False)
            ):
                errors.append(
                    {
                        "index": index,
                        "note_id": note_id,
                        "error": "Item changes nothing: it carries neither content nor tags. "
                        "Use edit_note to change title or folder.",
                    }
                )
                continue
            try:
                edit_result = apply_edit(
                    old_content,
                    raw.get("mode", "append"),
                    content=raw.get("content"),
                    old_str=raw.get("old_str"),
                    new_str=raw.get("new_str"),
                    target_heading=raw.get("target_heading"),
                    replace_all=bool(raw.get("replace_all", False)),
                )
            except ValueError as e:
                errors.append({"index": index, "note_id": note_id, "error": str(e)})
                continue
            new_content = edit_result.body
            try:
                links = workspace_links.validate(new_content, loc.note.folder)
            except BrokenWikilinkError as e:
                errors.append({"index": index, "note_id": note_id, "error": str(e)})
                continue
            raw_tags = raw.get("tags")
            current_tags = NoteTagService.normalize_tags(existing_meta.tags)
            new_tags = (
                NoteTagService.normalize_tags(raw_tags) if raw_tags is not None else current_tags
            )
            has_occurred_at = "occurred_at" in raw
            has_period = "period" in raw
            clear_date_metadata = bool(raw.get("clear_date_metadata", False))
            try:
                occurred_at, period = resolve_temporal_fields(
                    has_occurred_at=has_occurred_at,
                    has_period=has_period,
                    occurred_at=raw.get("occurred_at"),
                    period=raw.get("period"),
                    clear=clear_date_metadata,
                    fallback=(existing_meta.occurred_at, existing_meta.period),
                )
            except ValueError as e:
                errors.append({"index": index, "note_id": note_id, "error": str(e)})
                continue
            prepared.append(
                _PreparedEdit(
                    index=index,
                    note_id=note_id,
                    loc=loc,
                    meta=existing_meta,
                    old_content=old_content,
                    old_tags=current_tags,
                    new_content=new_content,
                    new_tags=new_tags,
                    occurred_at=occurred_at,
                    period=period,
                    links=links,
                    replaced=edit_result.replaced,
                )
            )

        if errors:
            return {"applied": False, "errors": errors}

        now = datetime.now(UTC).isoformat()
        n = len(prepared)
        items = [
            StagedWrite(
                relative=p.loc.relative,
                apply=partial(
                    write_note_file,
                    p.loc.filepath,
                    replace(
                        p.meta,
                        id=p.note_id,
                        title=p.loc.note.title,
                        tags=p.new_tags,
                        created_at=p.loc.note.created_at,
                        updated_at=now,
                        occurred_at=p.occurred_at,
                        period=p.period,
                    ),
                    p.new_content,
                ),
                restore=partial(
                    write_note_file,
                    p.loc.filepath,
                    replace(
                        p.meta,
                        id=p.note_id,
                        title=p.loc.note.title,
                        tags=p.old_tags,
                        created_at=p.loc.note.created_at,
                        updated_at=p.loc.note.updated_at,
                        occurred_at=p.loc.note.occurred_at,
                        period=p.loc.note.period,
                    ),
                    p.old_content,
                ),
            )
            for p in prepared
        ]
        with staged_note_write(git_repo, items, f"note: edit {n} note{'' if n == 1 else 's'}"):
            pass

        for p in prepared:
            self._crud_repo.update(
                p.note_id,
                owner_id=user_id,
                title=p.loc.note.title,
                content=p.new_content,
                tags=p.new_tags,
                updated_at=now,
                folder=p.loc.note.folder,
                occurred_at=p.occurred_at,
                period=p.period,
                bump_index_generation=True,
            )
        self._link_service.persist_many(
            ws_name,
            user_id,
            {p.note_id: p.links for p in prepared},
        )
        for p in prepared:
            self._tag_service.sync_tags(p.note_id, ws_name, user_id, p.new_tags, p.new_content)

        if self._cache is not None:
            self._cache.bump(ws_name, user_id)

        if self._indexer is not None:
            index_payload = [
                {
                    "id": p.note_id,
                    "title": p.loc.note.title,
                    "content": p.new_content,
                    "index_generation": p.loc.note.index_generation + 1,
                }
                for p in prepared
            ]
            defer_workspace_postprocess(
                ws_path, partial(self._indexer.index_many, ws_name, user_id, index_payload)
            )

        results = [
            {
                "index": p.index,
                "note_id": p.note_id,
                "replaced": p.replaced,
                "warnings": wikilink_warnings(p.links),
            }
            for p in prepared
        ]
        for p in prepared:
            logger.info("note_updated", note_id=p.note_id, folder=p.loc.note.folder)
        logger.info("notes_edited_batch", ws=ws_name, count=len(prepared))
        return {"applied": True, "results": results}

    @workspace_write_transaction
    def delete(
        self, note_id: str, owner_id: str, ws_path: str, expected_sha: str | None = None
    ) -> dict:
        """Delete a note. expected_sha (MCP callers) must match the note's HEAD
        commit; ``None`` (REST API) skips the check. A missing file (orphaned DB
        row) also skips it — there is no version the caller could have read, and
        the delete is then pure index cleanup."""
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        file_exists = Path(filepath).exists()
        relative = ""
        if file_exists:
            relative = str(Path(filepath).relative_to(ws_path))
            if expected_sha is not None and not sha_is_fresh(
                current_head_sha(ws_path, relative), expected_sha
            ):
                return stale_payload(note_id)
        workspace_links = self._link_service.for_workspace(note.workspace, owner_id)
        affected_sources = workspace_links.affected_sources({note.title})
        affected_sources.discard(note_id)  # this source is synchronously deleted below
        if file_exists:
            GitRepository(ws_path).delete_file(relative, f"note: delete {note_id}")
        with (
            self._crud_repo.operation(
                "delete", note_id=note_id, workspace=note.workspace, owner_id=owner_id
            ) as operation,
            operation.session.begin(),
        ):
            self._teardown_note(operation.session, note)
        if self._cache is not None:
            self._cache.bump(note.workspace, owner_id)
        logger.info("note_deleted", note_id=note_id)
        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(owner_id, note.workspace, affected_sources)
        return {"note_id": note_id}

    @workspace_write_transaction
    def delete_many(
        self,
        user_id: str,
        ws_name: str,
        ws_path: str,
        deletes: list[dict],
    ) -> dict:
        """Delete multiple notes in one Git commit and one DB transaction.

        All-or-nothing: an invalid item (missing note, duplicate note_id, stale expected_sha)
        rejects the whole batch — nothing is deleted. Each input dict has ``note_id`` and
        ``expected_sha``. The latter is the current HEAD commit sha (from get_history), proving the
        caller has seen the version it is about to destroy; a mismatch rejects the item without
        revealing the current sha, forcing a real re-read instead of a blind retry.
        """
        if not deletes:
            raise ValueError("Batch usuwania nie może być pusty.")

        # One open repo for the whole batch: staleness checks during validation and
        # the single atomic commit afterwards, instead of re-opening per item.
        git_repo = GitRepository(ws_path)
        note_ids = [str(raw.get("note_id", "")).strip() for raw in deletes]
        located = self._locate_batch(note_ids, user_id, ws_path, git_repo)
        errors: list[dict] = []
        prepared: list[_ValidatedDestructiveItem] = []
        for item in self._validate_destructive_items(deletes, note_ids, located):
            if isinstance(item, _BatchValidationError):
                errors.append(item.as_dict())
            else:
                prepared.append(item)

        if errors:
            return {"applied": False, "errors": errors}

        workspace_links = self._link_service.for_workspace(ws_name, user_id)
        affected_sources = workspace_links.affected_sources({p.loc.note.title for p in prepared})
        affected_sources.difference_update(p.note_id for p in prepared)

        n = len(prepared)
        git_repo.delete_files(
            [p.loc.relative for p in prepared], f"note: delete {n} note{'' if n == 1 else 's'}"
        )

        with (
            self._crud_repo.operation(
                "delete_many", workspace=ws_name, owner_id=user_id, count=len(prepared)
            ) as operation,
            operation.session.begin(),
        ):
            for p in prepared:
                self._teardown_note(operation.session, p.loc.note)
        for p in prepared:
            logger.info("note_deleted", note_id=p.note_id)

        if self._cache is not None:
            self._cache.bump(ws_name, user_id)
        logger.info("notes_deleted_batch", ws=ws_name, count=len(prepared))
        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(user_id, ws_name, affected_sources)
        results = [{"index": p.index, "note_id": p.note_id} for p in prepared]
        return {"applied": True, "results": results}

    def list_notes(
        self,
        ws_name: str,
        owner_id: str,
        tags: list[str] | None = None,
        limit: int | None = 20,
        folder: str | None = None,
        include_descendants: bool = True,
        sort: str = "default",
    ) -> list[dict]:
        return self._crud_repo.list_notes(
            ws_name,
            owner_id=owner_id,
            tags=tags,
            limit=limit,
            folder=folder,
            include_descendants=include_descendants,
            sort=sort,
            _tag_repo=self._tag_repo if tags else None,
        )

    def entries_in(
        self, ws_name: str, owner_id: str, period: str, folder: str | None = None
    ) -> list[dict]:
        parsed = parse_period_key(period)
        normalized_folder = normalize_folder(folder) if folder is not None else None
        return self._crud_repo.entries_in(
            ws_name,
            owner_id,
            parsed.start.isoformat(),
            parsed.next().start.isoformat(),
            folder=normalized_folder,
        )

    def temporal_backfill_preview(
        self, ws_name: str, owner_id: str, ws_path: str
    ) -> dict[str, list[dict]]:
        """Find safe title-derived temporal metadata without changing any note."""
        ambiguous: list[dict] = []
        skipped: list[dict] = []
        pending: list[tuple[dict, str]] = []
        for note in self._crud_repo.list_notes(ws_name, owner_id, limit=None):
            kind, payload = _classify_temporal_note(
                note["note_id"], note["title"], note["folder"], note["occurred_at"], note["period"]
            )
            if kind == "ambiguous":
                ambiguous.append(payload)
            elif kind == "skipped":
                skipped.append(payload)
            else:
                relative = str(
                    Path(note_filepath(ws_path, note["folder"], note["title"])).relative_to(ws_path)
                )
                pending.append((payload, relative))
        # One batched git walk for every candidate's sha instead of one Repo-open-and-walk
        # per note (_locate_batch does the same batching for every other batch operation).
        head_shas = GitRepository(ws_path).head_shas_for_paths(
            [relative for _, relative in pending]
        )
        candidates = [{**payload, "sha": head_shas[relative]} for payload, relative in pending]
        return {"candidates": candidates, "ambiguous": ambiguous, "skipped": skipped}

    @workspace_write_transaction
    def apply_temporal_backfill(
        self, ws_name: str, owner_id: str, ws_path: str, candidates: list[dict]
    ) -> dict:
        """Apply an explicitly previewed backfill batch without touching search chunks."""
        if not candidates:
            raise ValueError("candidates must not be empty")
        requested_ids = [item.get("note_id") for item in candidates]
        if any(not isinstance(i, str) or not i for i in requested_ids):
            raise ValueError("candidates: every item needs a non-empty string note_id")
        if len(set(requested_ids)) != len(requested_ids):
            raise ValueError("candidates: note_id values must be unique")
        # One GitRepository for both the batch locate and the staged write below — a
        # second open re-parses refs/pack indexes for no reason (see _locate_batch's
        # docstring and edit_many/delete_many for the established one-repo-per-batch shape).
        git_repo = GitRepository(ws_path)
        located = self._locate_batch(cast(list[str], requested_ids), owner_id, ws_path, git_repo)
        prepared: list[tuple[LocatedNote, NoteFrontmatter, str, str | None, str | None]] = []
        for item in candidates:
            loc = located.get(item["note_id"])
            if loc is None or not loc.file_exists:
                raise ValueError("backfill preview is stale; run preview again")
            # item["sha"] is None for a note with no matching git history yet (a brand
            # new file). sha_is_fresh treats a falsy expected_sha as never fresh, so that
            # case is compared directly instead: still-no-history is fresh, anything else
            # is a real change since preview.
            fresh = (
                loc.head_sha is None
                if item["sha"] is None
                else sha_is_fresh(loc.head_sha, item["sha"])
            )
            if not fresh:
                raise ValueError("backfill preview is stale; run preview again")
            # Re-derive the candidate from the current row instead of re-running preview
            # over the whole workspace (this method already holds the write lock; a full
            # rescan here would block every other write for O(workspace size), not
            # O(len(candidates))).
            kind, expected = _classify_temporal_note(
                loc.note.id, loc.note.title, loc.note.folder, loc.note.occurred_at, loc.note.period
            )
            if kind != "candidate" or {**expected, "sha": item["sha"]} != item:
                raise ValueError("backfill preview is stale; run preview again")
            meta, content = read_note_file(loc.filepath)
            occurred_at = item["value"] if item["field"] == "occurred_at" else None
            period = item["value"] if item["field"] == "period" else None
            prepared.append((loc, meta, content, occurred_at, period))
        items = [
            StagedWrite(
                relative=loc.relative,
                apply=partial(
                    write_note_file,
                    loc.filepath,
                    replace(meta, occurred_at=occurred_at, period=period),
                    content,
                ),
                restore=partial(write_note_file, loc.filepath, meta, content),
            )
            for loc, meta, content, occurred_at, period in prepared
        ]
        message = f"note: backfill temporal metadata ({len(items)} notes)"
        with staged_note_write(git_repo, items, message):
            pass
        with (
            self._crud_repo.operation(
                "backfill_temporal_metadata",
                workspace=ws_name,
                owner_id=owner_id,
                count=len(prepared),
            ) as operation,
            operation.session.begin(),
        ):
            for loc, _meta, _content, occurred_at, period in prepared:
                self._crud_repo.update_in_session(
                    operation.session,
                    loc.note.id,
                    owner_id=owner_id,
                    updated_at=loc.note.updated_at,
                    occurred_at=occurred_at,
                    period=period,
                    bump_index_generation=False,
                )
        if self._cache is not None:
            self._cache.bump(ws_name, owner_id)
        logger.info("temporal_backfill_applied", ws=ws_name, count=len(prepared))
        return {"applied": len(prepared)}

    def clear_workspace_data(self, ws_name: str, owner_id: str) -> None:
        """Delete every note-related row for a workspace: tags, chunks (+ FTS/vec),
        notes, and links. Used by workspace deletion. NOT used by reconcile/reindex
        (see reconcile_paths) — a wipe-then-rebuild has no window where the deletion
        safety valve could measure anything, and a crash mid-run loses every row."""
        # FK ordering: chunks must be deleted before notes (note_chunks.note_id FK).
        with self._crud_repo.operation(
            "clear_workspace_data", workspace=ws_name, owner_id=owner_id
        ) as operation:
            session = operation.session
            with session.begin():
                self._tag_repo.delete_workspace_tags_in_session(session, ws_name, owner_id)
                self._chunk_repo.delete_for_workspace(ws_name, owner_id, session)
                self._crud_repo.delete_for_workspace(ws_name, owner_id, session)
                self._link_repo.delete_workspace_links_in_session(session, ws_name, owner_id)
                self._link_service.delete_dangling_for_workspace_in_session(
                    session, ws_name, owner_id
                )
        logger.info("workspace_data_cleared", ws=ws_name, owner_id=owner_id)

    @workspace_write_transaction
    def reconcile_paths(
        self, ws_name: str, owner_id: str, ws_path: str, paths: Iterable[str]
    ) -> ReconcileReport:
        """Re-derive DB rows from disk for exactly the given workspace-relative paths.

        A missing file (in scope, no id found there) removes its row via ``_teardown_note``;
        a new id inserts one; drifted folder/title/tags/created_at updates it. No wipe:
        reconciling every path in scope IS the rebuild, which is what makes the deletion
        safety valve below meaningful (there's nothing to measure a wipe's blast radius
        against).

        Explicit contract decisions (issue #107 asks these be written down, not just coded):
        - Chunks are re-derived UNCONDITIONALLY for every present note. ``notes`` has no
          content column, so there is nothing to diff content against, and
          ``replace_chunks`` is idempotent — re-deriving is cheap-correct rather than a
          partial-repair guess. Chunk re-derivation is not what "updated" reports; that's
          folder/title/tags/created_at drift only.
        - Indexing failures are swallowed per note (``NoteIndexer.index_many``'s existing
          contract — matches today's reindex): one bad note logs and is skipped, it never
          aborts the reconcile.
        - A file that fails to parse is left alone entirely — logged, never routed into
          deletion, since "unreadable" is not evidence the note is gone.
        - Two files sharing an id: the first (sorted path order) is reconciled; the rest are
          reported in ``duplicate_ids`` and left untouched — never last-write-wins.
        - A file with no ``id`` is adopted, not skipped: a fresh id is generated and written
          back into its frontmatter (every other key preserved via
          ``NoteFrontmatter.extras``), then it is treated like any newly-present note. All
          adoptions in one run share a single git commit.
        """
        start = time.monotonic()
        root = Path(ws_path)
        scoped_paths = set(paths)

        present: dict[str, _PresentFile] = {}
        duplicate_ids: list[str] = []
        unreadable_paths: list[str] = []
        adoption_candidates: list[_AdoptionCandidate] = []
        for relative in sorted(scoped_paths):
            filepath = root / relative
            if not filepath.exists():
                continue
            try:
                meta, content = read_note_file(str(filepath))
            except Exception as e:
                unreadable_paths.append(relative)
                logger.opt(exception=e).warning(
                    "reconcile_unreadable_file", ws=ws_name, path=relative
                )
                continue
            if not meta.id:
                adoption_candidates.append(
                    _AdoptionCandidate(
                        relative=relative, filepath=filepath, meta=meta, content=content
                    )
                )
                continue
            if meta.id in present:
                duplicate_ids.append(meta.id)
                logger.warning(
                    "reconcile_duplicate_note_id",
                    ws=ws_name,
                    note_id=meta.id,
                    path=relative,
                    kept_path=present[meta.id].relative,
                )
                continue
            present[meta.id] = _present_file(
                meta.id, meta, content, note_folder(ws_path, filepath), relative
            )

        adopted_ids: list[str] = []
        if adoption_candidates:
            items: list[StagedWrite] = []
            pending: list[tuple[str, _AdoptionCandidate]] = []
            for candidate in adoption_candidates:
                new_id = generate(size=7)
                new_meta = replace(candidate.meta, id=new_id)
                original_bytes = candidate.filepath.read_bytes()
                items.append(
                    StagedWrite(
                        relative=candidate.relative,
                        apply=partial(
                            write_note_file, str(candidate.filepath), new_meta, candidate.content
                        ),
                        restore=partial(_restore_bytes, candidate.filepath, original_bytes),
                    )
                )
                pending.append((new_id, candidate))
            n = len(items)
            with staged_note_write(
                GitRepository(ws_path), items, f"note: adopt {n} file{'' if n == 1 else 's'}"
            ):
                pass
            for new_id, candidate in pending:
                present[new_id] = _present_file(
                    new_id,
                    candidate.meta,
                    candidate.content,
                    note_folder(ws_path, candidate.filepath),
                    candidate.relative,
                )
                adopted_ids.append(new_id)
                logger.info(
                    "reconcile_note_adopted", ws=ws_name, note_id=new_id, path=candidate.relative
                )

        present_ids = set(present)
        # A path that failed to parse is not evidence its row is gone — drop it from the
        # missing-detection scope entirely, so an unreadable file can never look deleted.
        missing_scope = scoped_paths - set(unreadable_paths)
        indexed = self._crud_repo.list_paths(ws_name, owner_id)
        scoped_row_ids = {
            n.note_id
            for n in indexed
            if str(Path(note_filepath(ws_path, n.folder, n.title)).relative_to(ws_path))
            in missing_scope
        }
        missing_ids = scoped_row_ids - present_ids

        total = len(indexed)
        if missing_ids and len(missing_ids) >= _RECONCILE_MIN_DELETE_FLOOR:
            ratio = len(missing_ids) / total if total else 1.0
            if ratio > _RECONCILE_MAX_DELETE_RATIO:
                raise ValueError(
                    f"Reconcile in workspace '{ws_name}' would delete {len(missing_ids)} of "
                    f"{total} notes ({ratio:.0%}), above the "
                    f"{_RECONCILE_MAX_DELETE_RATIO:.0%} safety threshold. Refusing — check "
                    "the workspace path and disk mount before retrying."
                )

        missing_notes = self._crud_repo.get_many(list(missing_ids), owner_id) if missing_ids else []
        existing_by_id = (
            {n.id: n for n in self._crud_repo.get_many(list(present_ids), owner_id)}
            if present_ids
            else {}
        )

        inserted: list[str] = []
        updated: list[str] = []
        unchanged = 0
        # Only identity changes (folder/title) can move which source resolves to which
        # target — tags/created_at/updated_at drift alone never changes link resolution,
        # so it must not requeue every backlink (matches edit_note's identity_changed gate).
        changed_titles: set[str] = {n.title for n in missing_notes}
        for note_id, pf in present.items():
            existing = existing_by_id.get(note_id)
            if existing is None:
                inserted.append(note_id)
                changed_titles.add(pf.title)
                continue
            identity_changed = existing.folder != pf.folder or existing.title != pf.title
            drifted = (
                identity_changed
                or json.loads(existing.tags or "[]") != pf.tags
                or existing.created_at != pf.created_at
                or existing.updated_at != pf.updated_at
                or existing.occurred_at != pf.occurred_at
                or existing.period != pf.period
            )
            if drifted:
                updated.append(note_id)
                if identity_changed:
                    changed_titles.add(existing.title)
                    changed_titles.add(pf.title)
            else:
                unchanged += 1

        # Computed against the PRE-mutation graph, same ordering as delete()/save(): a
        # removed note's own row (and its backlinks) must still be resolvable here, or
        # target_ids_for_titles finds nothing and sources pointing at it never heal.
        affected = self._link_service.for_workspace(ws_name, owner_id).affected_sources(
            changed_titles
        )
        affected -= present_ids  # every present note gets a fresh resolution below anyway
        # The removed notes themselves are torn down synchronously above — same reason
        # delete()/delete_many() discard their own note_id from affected_sources before
        # enqueuing, rather than leaving a dirty marker for a row that no longer exists.
        affected -= missing_ids

        with (
            self._crud_repo.operation(
                "reconcile_paths",
                workspace=ws_name,
                owner_id=owner_id,
                present=len(present),
                missing=len(missing_ids),
            ) as operation,
            operation.session.begin(),
        ):
            session = operation.session
            for note in missing_notes:
                self._teardown_note(session, note)
            for note_id in inserted:
                pf = present[note_id]
                self._crud_repo.insert_in_session(
                    session,
                    Note(
                        id=note_id,
                        workspace=ws_name,
                        owner_id=owner_id,
                        title=pf.title,
                        folder=pf.folder,
                        tags=json.dumps(pf.tags),
                        created_at=pf.created_at,
                        updated_at=pf.updated_at,
                        occurred_at=pf.occurred_at,
                        period=pf.period,
                    ),
                )
            for note_id in updated:
                pf = present[note_id]
                self._crud_repo.update_in_session(
                    session,
                    note_id,
                    owner_id=owner_id,
                    title=pf.title,
                    tags=pf.tags,
                    updated_at=pf.updated_at,
                    folder=pf.folder,
                    created_at=pf.created_at,
                    occurred_at=pf.occurred_at,
                    period=pf.period,
                    bump_index_generation=True,
                )

        # Tags/links/chunks: batched, own commits — same shape as save()/_apply_tag_change,
        # never folded into the note-row transaction above.
        workspace_links = self._link_service.for_workspace(ws_name, owner_id)
        resolutions = {}
        tagged_by_note = {}
        generation_by_id: dict[str, int] = {}
        for note_id, pf in present.items():
            tagged_by_note[note_id] = NoteTagService.tagged(pf.tags, pf.content)
            resolutions[note_id] = workspace_links.resolve(pf.content, pf.folder)
            if note_id in inserted:
                generation_by_id[note_id] = 1
            elif note_id in updated:
                generation_by_id[note_id] = existing_by_id[note_id].index_generation + 1
            else:
                generation_by_id[note_id] = existing_by_id[note_id].index_generation
        self._tag_repo.sync_note_tags_many(ws_name, owner_id, tagged_by_note)
        self._link_service.persist_many(ws_name, owner_id, resolutions)

        if self._indexer is not None:

            def _reindex_chunks() -> None:
                assert self._indexer is not None  # narrowed by the outer guard
                try:
                    self._indexer.index_many(
                        ws_name,
                        owner_id,
                        [
                            {
                                "id": note_id,
                                "title": present[note_id].title,
                                "content": present[note_id].content,
                                "index_generation": generation_by_id[note_id],
                            }
                            for note_id in present
                        ],
                    )
                except Exception as e:
                    logger.opt(exception=e).warning("reconcile_chunk_index_failed", ws=ws_name)

            # Deferred past lock release, like save()'s _index call — chunking a whole
            # workspace is CPU work that must not hold other writers behind the git lock.
            defer_workspace_postprocess(ws_path, _reindex_chunks)

        if self._cache is not None:
            self._cache.bump(ws_name, owner_id)

        if self._reconcile_repo is not None:
            self._reconcile_repo.mark_and_enqueue(owner_id, ws_name, affected)

        logger.info(
            "reconcile_complete",
            ws=ws_name,
            inserted=len(inserted),
            updated=len(updated),
            removed=len(missing_ids),
            unchanged=unchanged,
            duplicates=len(duplicate_ids),
            unreadable=len(unreadable_paths),
            adopted=len(adopted_ids),
            duration_ms=round((time.monotonic() - start) * 1000),
        )
        return ReconcileReport(
            inserted=inserted,
            updated=updated,
            removed=sorted(missing_ids),
            unchanged=unchanged,
            duplicate_ids=duplicate_ids,
            unreadable_paths=unreadable_paths,
            adopted=adopted_ids,
        )

    def reindex(self, ws_name: str, owner_id: str, ws_path: str) -> dict:
        """Full-workspace repair: reconcile every path that exists on disk, plus every
        path a DB row currently claims — the union, so a row whose computed path never
        matched any file on disk (stale sanitization, a prior bug's residue) is still
        caught and repaired, not just files that happen to exist right now."""
        disk_paths = set(iter_note_paths(ws_path))
        indexed = self._crud_repo.list_paths(ws_name, owner_id)
        db_paths = {
            str(Path(note_filepath(ws_path, n.folder, n.title)).relative_to(ws_path))
            for n in indexed
        }
        report = self.reconcile_paths(ws_name, owner_id, ws_path, disk_paths | db_paths)
        adopted_clause = f", {len(report.adopted)} adopted" if report.adopted else ""
        return {
            "message": (
                f"Reconciled workspace '{ws_name}': {len(report.inserted)} inserted, "
                f"{len(report.updated)} updated, {len(report.removed)} removed, "
                f"{report.unchanged} unchanged{adopted_clause}."
            ),
            "count": report.present,
        }

    @workspace_write_transaction
    def restore_version(
        self,
        note_id: str,
        sha: str,
        owner_id: str,
        ws_path: str,
        expected_sha: str | None = None,
    ) -> dict:
        """Restore a past version over HEAD. expected_sha (MCP callers) proves the
        caller saw the HEAD it is about to overwrite; ``None`` (REST API) skips it."""
        version = self._version_service.get_version(note_id, sha, owner_id, ws_path)
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        relative = str(Path(note_filepath(ws_path, note.folder, note.title)).relative_to(ws_path))
        current_sha = current_head_sha(ws_path, relative)
        if current_sha is None:
            raise ValueError(f"Notatka {note_id} nie ma historii commitów.")
        if expected_sha is not None and not sha_is_fresh(current_sha, expected_sha):
            return stale_payload(note_id)
        # Restore proves intent by construction: update()'s own staleness check is
        # satisfied with the head sha just read.
        return self.update(
            note_id,
            owner_id=owner_id,
            ws_path=ws_path,
            expected_sha=current_sha,
            content=version["content"],
            tags=version["tags"],
            extras=version["extras"],
            occurred_at=version["occurred_at"],
            period=version["period"],
        )

    # Delegation to peer services (public API unchanged):
    def backlinks(self, note_id: str, owner_id: str, include_meta: bool = False) -> list[dict]:
        return self._link_service.backlinks(note_id, owner_id, include_meta)

    def outlinks(self, note_id: str, owner_id: str, include_meta: bool = False) -> list[dict]:
        return self._link_service.outlinks(note_id, owner_id, include_meta)

    def links(
        self,
        note_id: str,
        owner_id: str,
        include_meta: bool = False,
        include_cross_workspace: bool = True,
    ) -> dict | None:
        return self._link_service.links(note_id, owner_id, include_meta, include_cross_workspace)

    def link_resolver(self, ws_name: str, owner_id: str, source_folder: str = "") -> LinkResolver:
        resolver: LinkResolver | None = None

        def resolve(target: str):
            nonlocal resolver
            if resolver is None:
                resolver = self._link_service.for_workspace(ws_name, owner_id).resolver(
                    source_folder
                )
            return resolver(target)

        return resolve

    def xws_link_resolver(self, owner_id: str):
        return self._link_service.xws_link_resolver(owner_id)

    def add_tags(self, note_id: str, owner_id: str, ws_path: str, tags: list[str]) -> dict:
        return self._tag_service.add_tags(note_id, owner_id, ws_path, tags)

    def remove_tags(self, note_id: str, owner_id: str, ws_path: str, tags: list[str]) -> dict:
        return self._tag_service.remove_tags(note_id, owner_id, ws_path, tags)

    def set_tags(
        self,
        note_id: str,
        owner_id: str,
        ws_path: str,
        tags: list[str],
        expected_sha: str | None = None,
    ) -> dict:
        return self._tag_service.set_tags(note_id, owner_id, ws_path, tags, expected_sha)

    def rename_tag(
        self,
        old: str,
        new: str,
        *,
        owner_id: str,
        ws_name: str,
        ws_path: str,
        merge: bool = False,
    ) -> dict:
        return self._tag_service.rename_tag(
            old, new, owner_id=owner_id, ws_name=ws_name, ws_path=ws_path, merge=merge
        )

    def tag_tree(self, ws_name: str, owner_id: str) -> list[dict]:
        return self._tag_repo.tag_tree(ws_name, owner_id)

    def tag_counts(
        self,
        ws_name: str,
        owner_id: str,
        folder: str | None = None,
        include_subfolders: bool = True,
    ) -> list[dict]:
        return self._tag_repo.tag_counts(ws_name, owner_id, folder, include_subfolders)

    def notes_by_tag(
        self,
        ws_name: str,
        owner_id: str,
        path: str,
        include_descendants: bool = True,
        limit: int | None = None,
    ) -> list[dict]:
        return self._tag_repo.notes_by_tag(ws_name, owner_id, path, include_descendants, limit)

    def search(
        self,
        query: str,
        workspaces: list[str],
        owner_id: str,
        limit: int = 10,
        folder: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        return self._search_service.search(
            query, workspaces, owner_id, limit, folder=folder, tags=tags
        )

    async def search_async(
        self,
        query: str,
        workspaces: list[str],
        owner_id: str,
        limit: int = 10,
        folder: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        return await self._search_service.search_async(
            query, workspaces, owner_id, limit, folder=folder, tags=tags
        )

    def get_history(self, note_id: str, owner_id: str, ws_path: str, limit: int = 50) -> list[dict]:
        return self._version_service.get_history(note_id, owner_id, ws_path, limit)

    def get_version(self, note_id: str, sha: str, owner_id: str, ws_path: str) -> dict:
        return self._version_service.get_version(note_id, sha, owner_id, ws_path)

    def move(self, note_id: str, owner_id: str, ws_path: str, folder: str) -> dict:
        return self._folder_service.move(note_id, owner_id, ws_path, folder)

    def move_folder(
        self, src: str, dst: str, *, owner_id: str, ws_path: str, workspace: str
    ) -> dict:
        return self._folder_service.move_folder(
            src, dst, owner_id=owner_id, ws_path=ws_path, workspace=workspace
        )

    def prune_empty_folders(self, ws_path: str) -> dict:
        return self._folder_service.prune_empty_folders(ws_path)

    def list_folders(self, ws_path: str) -> list[str]:
        return self._folder_service.list_folders(ws_path)
