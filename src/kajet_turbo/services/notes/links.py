import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from itertools import chain

from sqlmodel import Session

from kajet_turbo.log import logger
from kajet_turbo.markdown import (
    BrokenWikilinkError,
    IndexedNote,
    LinkIndex,
    LinkResolution,
    LinkResolver,
    join_target,
    note_explorer_url,
    resolve_content_links,
    rewrite_wikilinks,
)
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.git import GitRepository, workspace_write_transaction
from kajet_turbo.repositories.notes import NoteLinkRepository, NoteRepository
from kajet_turbo.services.notes.staged_write import StagedWrite, staged_note_write
from kajet_turbo.workspace import locate_note, path_segments, read_note_file, write_note_file

# (old, new) identity of a note that was moved and/or renamed.
type NoteMove = tuple[IndexedNote, IndexedNote]


def wikilink_warnings(links: LinkResolution) -> list[dict]:
    """Public warning payloads for a content-resolution result."""
    ambiguous = [
        {
            "kind": "ambiguous_wikilink",
            "target": item.target,
            "resolved_to": join_target(item.chosen.folder, item.chosen.title),
            "alternatives": [join_target(n.folder, n.title) for n in item.alternatives],
        }
        for item in links.ambiguous
    ]
    case_corrected = [
        {
            "kind": "case_corrected_wikilink",
            "target": item.target,
            "resolved_to": join_target(item.chosen.folder, item.chosen.title),
            "alternatives": [],
        }
        for item in links.case_corrected
    ]
    return sorted(ambiguous + case_corrected, key=lambda warning: warning["target"])


@dataclass(frozen=True, slots=True)
class WorkspaceLinks:
    """One immutable wikilink-resolution snapshot for a workspace operation."""

    _service: NoteLinkService
    ws_name: str
    owner_id: str
    paths: tuple[IndexedNote, ...]
    index: LinkIndex

    def resolve(self, content: str, source_folder: str) -> LinkResolution:
        return self._service._resolve_links(self, content, source_folder)

    def validate(self, content: str, source_folder: str) -> LinkResolution:
        return self._service._validate_wikilinks(self, content, source_folder)

    def resolver(self, source_folder: str = "") -> LinkResolver:
        return lambda target: self.index.resolve(target, source_folder)

    @workspace_write_transaction
    def rewrite_backlinks(self, moves: list[NoteMove], ws_path: str) -> None:
        self._service._rewrite_backlinks(self, moves, ws_path)

    def target_ids_for_titles(self, titles: set[str]) -> list[str]:
        """Current target note ids whose title may be affected by an identity change."""
        return [note.note_id for note in self.paths if note.title in titles]

    def affected_sources(
        self, titles: set[str], include_source_ids: Iterable[str] = ()
    ) -> set[str]:
        """Sources whose resolution can change when one of ``titles`` appears or moves."""
        return self._service._affected_sources(self, titles, include_source_ids)


class NoteLinkService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        link_repo: NoteLinkRepository,
        dangling_repo: DanglingLinkRepository | None,
        link_validation_enabled: Callable[[str, str], bool] | None,
    ):
        self._crud_repo = crud_repo
        self._link_repo = link_repo
        self._dangling_repo = dangling_repo
        self._link_validation_enabled = link_validation_enabled

    def _links_validated(self, ws_name: str, owner_id: str) -> bool:
        if self._link_validation_enabled is None:
            return True
        return self._link_validation_enabled(ws_name, owner_id)

    def for_workspace(
        self, ws_name: str, owner_id: str, extra: Iterable[IndexedNote] = ()
    ) -> WorkspaceLinks:
        """Snapshot of the workspace's notes for wikilink resolution. ``extra`` adds notes
        that don't exist in the DB yet (a batch being saved) so in-batch links resolve."""
        paths = tuple(chain(self._crud_repo.list_paths(ws_name, owner_id), extra))
        return WorkspaceLinks(self, ws_name, owner_id, paths, LinkIndex(paths))

    def _resolve_links(
        self,
        workspace: WorkspaceLinks,
        content: str,
        source_folder: str,
    ) -> LinkResolution:
        """Resolve every wikilink in ``content`` without judging the result.

        Intra-workspace targets resolve against the operation's explicit snapshot.
        ``[[note:ID]]`` cross-workspace links are resolved by note ID and folded into
        ``resolved_ids`` — a missing ID is simply dropped, never reported as broken.
        """
        resolution = resolve_content_links(workspace.index, content, source_folder)
        if not resolution.xws_ids:
            return resolution
        xws_found = {n.id for n in self._crud_repo.get_many(resolution.xws_ids, workspace.owner_id)}
        return replace(resolution, resolved_ids=resolution.resolved_ids | xws_found)

    def _validate_wikilinks(
        self,
        workspace: WorkspaceLinks,
        content: str,
        source_folder: str,
    ) -> LinkResolution:
        """Resolve plus the workspace's broken-link validation policy."""
        resolution = self._resolve_links(workspace, content, source_folder)
        if resolution.broken and self._links_validated(workspace.ws_name, workspace.owner_id):
            raise BrokenWikilinkError(resolution.broken)
        return resolution

    def persist(self, note_id: str, ws_name: str, owner_id: str, links: LinkResolution) -> None:
        """Store one note's resolution outcome: the link-graph edges for what resolved and
        the dangling rows for what did not — both halves, so no write path can drift."""
        self.persist_many(ws_name, owner_id, {note_id: links})

    def persist_many(
        self,
        ws_name: str,
        owner_id: str,
        resolutions: dict[str, LinkResolution],
        clear_source_ids: set[str] | None = None,
    ) -> None:
        """Atomically persist a reconciliation batch in one SQLite transaction."""
        clear_source_ids = set() if clear_source_ids is None else clear_source_ids
        if not resolutions and not clear_source_ids:
            return
        now = datetime.now(UTC).isoformat()
        with self._link_repo.operation(
            "persist_many",
            workspace=ws_name,
            owner_id=owner_id,
            sources=len(resolutions),
            cleared=len(clear_source_ids - resolutions.keys()),
        ) as operation:
            session = operation.session
            for source_id, links in resolutions.items():
                self._link_repo.replace_links_in_session(
                    session, source_id, ws_name, owner_id, links.resolved_ids
                )
                if self._dangling_repo is not None:
                    self._dangling_repo.replace_for_source_in_session(
                        session,
                        source_id,
                        ws_name,
                        owner_id,
                        links.broken_pairs,
                        now=now,
                    )
            for source_id in clear_source_ids - resolutions.keys():
                self._link_repo.delete_links_from_in_session(session, source_id)
                if self._dangling_repo is not None:
                    self._dangling_repo.delete_for_source_in_session(session, source_id)
            session.commit()

    def delete_dangling_for_source_in_session(self, session: Session, note_id: str) -> None:
        """Remove a deleted source's dangling rows without owning the transaction."""
        if self._dangling_repo is not None:
            self._dangling_repo.delete_for_source_in_session(session, note_id)

    def delete_dangling_for_workspace_in_session(
        self, session: Session, ws_name: str, owner_id: str
    ) -> None:
        """Remove a workspace's dangling rows without owning the transaction."""
        if self._dangling_repo is not None:
            self._dangling_repo.delete_for_workspace_in_session(session, owner_id, ws_name)

    def _affected_sources(
        self,
        workspace: WorkspaceLinks,
        titles: set[str],
        include_source_ids: Iterable[str],
    ) -> set[str]:
        """Collect graph and dangling sources before an identity-changing write.

        All current same-title candidates matter: adding, deleting, moving, or renaming
        one candidate can change the deterministic winner even for an edge that currently
        points to another candidate. Moved sources are included separately because their
        own folder participates in proximity ranking.
        """
        target_ids = workspace.target_ids_for_titles(titles)
        sources = self._link_repo.backlinks_many(target_ids, same_workspace=workspace.ws_name)
        if self._dangling_repo is not None:
            sources.update(
                self._dangling_repo.sources_for_titles(
                    workspace.owner_id, workspace.ws_name, titles
                )
            )
        sources.update(include_source_ids)
        return sources

    def backlinks(
        self,
        note_id: str,
        owner_id: str,
        include_meta: bool = False,
        include_cross_workspace: bool = True,
    ) -> list[dict]:
        same_ws: str | None = None
        if not include_cross_workspace:
            note = self._crud_repo.get(note_id, owner_id=owner_id)
            same_ws = note.workspace if note is not None else None
        return self._resolve_link_notes(
            self._link_repo.backlinks(note_id, same_workspace=same_ws),
            owner_id,
            include_meta,
        )

    def outlinks(self, note_id: str, owner_id: str, include_meta: bool = False) -> list[dict]:
        return self._resolve_link_notes(self._link_repo.outlinks(note_id), owner_id, include_meta)

    def links(
        self,
        note_id: str,
        owner_id: str,
        include_meta: bool = False,
        include_cross_workspace: bool = True,
    ) -> dict | None:
        if self._crud_repo.get(note_id, owner_id=owner_id) is None:
            return None
        return {
            "backlinks": self.backlinks(note_id, owner_id, include_meta, include_cross_workspace),
            "outlinks": self.outlinks(note_id, owner_id, include_meta),
        }

    def xws_link_resolver(self, owner_id: str):
        def resolve(note_id: str) -> tuple[str, str] | None:
            note = self._crud_repo.get(note_id, owner_id=owner_id)
            if note is None:
                return None
            return note.title, note_explorer_url(note.workspace, note.folder, note.id)

        return resolve

    def _resolve_link_notes(
        self,
        note_ids: list[str],
        owner_id: str,
        include_meta: bool = False,
    ) -> list[dict]:
        """Map note_ids to ``{note_id, title, folder, workspace}``, skipping missing notes.
        With ``include_meta=True`` also includes ``tags`` and ``updated_at``."""
        notes = {note.id: note for note in self._crud_repo.get_many(note_ids, owner_id)}
        result = []
        for note_id in note_ids:
            note = notes.get(note_id)
            if note is None:
                continue
            entry: dict = {
                "note_id": note.id,
                "title": note.title,
                "folder": note.folder,
                "workspace": note.workspace,
            }
            if include_meta:
                entry["tags"] = json.loads(note.tags or "[]")
                entry["updated_at"] = note.updated_at
            result.append(entry)
        return result

    def _rewrite_backlinks(
        self, workspace: WorkspaceLinks, moves: list[NoteMove], ws_path: str
    ) -> None:
        """Rewrite wikilinks in every note that links to a moved/renamed note so they still
        resolve. Call after the DB rows hold the new paths; ``moves`` carries each note's
        old and new identity (one pair for a rename, a whole folder's worth for a folder
        move), so the pre-move index can be reconstructed and every source is rewritten and
        committed exactly once no matter how many moved notes it links to.

        Links are matched by *resolution* against the pre-move index — including short
        ``[[Title]]`` forms, which carry no path to compare — and rewritten to the shortest
        target that resolves to the note afterwards, keeping at least the author's segment
        count (``[[Old/T]]`` becomes ``[[New/T]]``, not ``[[A/New/T]]``). A bare title stays
        bare while it is still unambiguous; otherwise the path grows until it is.

        Only same-workspace backlinks are rewritten: cross-workspace links use ``[[note:ID]]``
        syntax which is ID-stable and needs no path update.

        Every affected source is staged, then committed together in one batch — via a raw
        write, deliberately not the full ``NoteService.update()`` pipeline. Four steps
        ``update()`` runs are skipped here, each for its own reason:

        - ``replace_links``: no-op by construction. A link's graph edge is keyed on
          ``(source_note_id, target_note_id)``; the rewritten target is verified to resolve
          to the same note, so the edge is already correct.
        - ``sync_tags``: the rewrite only touches wikilink path text, never frontmatter tags.
        - ``write_dangling``: this only rewrites links that already resolved (they're in the
          link graph in the first place), so no pair moves between resolved/dangling.
        - Search reindexing (chunks/FTS/embeddings): genuinely skipped, not a no-op. This
          method has no indexer reference, so a rewritten source note's search index keeps
          the OLD link text until that note's next real edit or a workspace-wide reindex.
          Accepted as a cosmetic, self-healing gap — not worth threading an indexer through
          this call for a stale snippet/chunk-offset window that closes on the next edit.
        """
        moved = {old.note_id: new for old, new in moves}
        source_ids = self._link_repo.backlinks_many(list(moved), same_workspace=workspace.ws_name)
        if not source_ids:
            return
        after = LinkIndex(moved.get(path.note_id, path) for path in workspace.paths)
        before = workspace.index
        old_folders = {old.note_id: old.folder for old, _ in moves}

        def rewrite(target: str, before_folder: str, after_folder: str) -> str | None:
            hit = before.resolve(target, before_folder)
            if hit is None or hit.note_id not in moved:
                return None
            return after.shortest_target(
                moved[hit.note_id], after_folder, min_segments=len(path_segments(target))
            )

        message = (
            f"note: rewrite wikilink {moves[0][0].title} -> {moves[0][1].title}"
            if len(moves) == 1
            else f"note: rewrite wikilinks after moving {len(moves)} notes"
        )
        items: list[StagedWrite] = []
        rewrites: list[tuple[str, str, str]] = []  # (note_id, new_body, updated_at)
        for src in self._crud_repo.get_many(sorted(source_ids), workspace.owner_id):
            loc = locate_note(src, ws_path)
            if not loc.file_exists:
                continue
            data = read_note_file(loc.filepath)
            old_content = data["content"]
            # A source that moved along with its targets (folder move) must be ranked from
            # where it *was* when its links were written, not from its new folder.
            new_body, changed = rewrite_wikilinks(
                old_content,
                partial(
                    rewrite,
                    before_folder=old_folders.get(src.id, src.folder),
                    after_folder=src.folder,
                ),
            )
            if not changed:
                continue
            tags = json.loads(src.tags or "[]")
            items.append(
                StagedWrite(
                    relative=loc.relative,
                    apply=partial(
                        write_note_file,
                        loc.filepath,
                        src.id,
                        src.title,
                        tags,
                        src.created_at,
                        src.updated_at,
                        new_body,
                    ),
                    restore=partial(
                        write_note_file,
                        loc.filepath,
                        src.id,
                        src.title,
                        tags,
                        src.created_at,
                        src.updated_at,
                        old_content,
                    ),
                )
            )
            rewrites.append((src.id, new_body, src.updated_at))

        if items:
            with staged_note_write(GitRepository(ws_path), items, message):
                pass
            for note_id, new_body, updated_at in rewrites:
                self._crud_repo.update(
                    note_id,
                    owner_id=workspace.owner_id,
                    content=new_body,
                    updated_at=updated_at,
                    bump_index_generation=True,
                )
        logger.info(
            "backlinks_rewritten",
            ws=workspace.ws_name,
            moved=len(moves),
            sources=len(source_ids),
            rewritten=len(rewrites),
        )
