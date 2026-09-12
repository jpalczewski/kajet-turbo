from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from itertools import chain

from sqlmodel import Session

from kajet_turbo.markdown import (
    BrokenWikilinkError,
    IndexedNote,
    LinkIndex,
    LinkResolution,
    LinkResolver,
    join_target,
    note_explorer_url,
    resolve_content_links,
)
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.git import GitRepository, workspace_write_transaction
from kajet_turbo.repositories.jobs import JobRepository
from kajet_turbo.repositories.notes import NoteLinkRepository, NoteRepository, NoteTagRepository
from kajet_turbo.services.notes.backlinks import BacklinkRewriter, NoteMove
from kajet_turbo.services.notes.graph import resolve_link_notes
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget


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
    corrected = [
        {
            "kind": "case_corrected_wikilink",
            "target": item.target,
            "resolved_to": join_target(item.chosen.folder, item.chosen.title),
            "alternatives": [],
        }
        for item in links.case_corrected
    ]
    return sorted(ambiguous + corrected, key=lambda warning: warning["target"])


@dataclass(frozen=True, slots=True)
class WorkspaceLinks:
    """One immutable wikilink-resolution snapshot for a workspace operation."""

    _service: NoteLinkService
    ws_name: str
    owner_id: str
    paths: tuple[IndexedNote, ...]
    index: LinkIndex

    def with_extra(self, extra: Iterable[IndexedNote]) -> WorkspaceLinks:
        return self._service._build(self.ws_name, self.owner_id, tuple(chain(self.paths, extra)))

    def resolve(self, content: str, source_folder: str) -> LinkResolution:
        return self._service._resolve_links(self, content, source_folder)

    def validate(self, content: str, source_folder: str) -> LinkResolution:
        return self._service._validate_wikilinks(self, content, source_folder)

    def resolver(self, source_folder: str = "") -> LinkResolver:
        return lambda target: self.index.resolve(target, source_folder)

    @workspace_write_transaction
    def rewrite_backlinks(self, moves: list[NoteMove], ws_path: str, repo: GitRepository) -> None:
        """Reuse the caller-opened repository while rewriting moved or renamed targets."""
        self._service._backlinks.rewrite_backlinks(self, moves, ws_path, repo)

    def target_ids_for_titles(self, titles: set[str]) -> list[str]:
        return [note.note_id for note in self.paths if note.title in titles]

    def affected_sources(
        self, titles: set[str], include_source_ids: Iterable[str] = ()
    ) -> set[str]:
        return self._service._affected_sources(self, titles, include_source_ids)


class NoteLinkService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        link_repo: NoteLinkRepository,
        tag_repo: NoteTagRepository,
        dangling_repo: DanglingLinkRepository | None,
        link_validation_enabled: Callable[[str, str], bool] | None,
        jobs: JobRepository,
    ):
        self._crud_repo = crud_repo
        self._link_repo = link_repo
        self._dangling_repo = dangling_repo
        self._link_validation_enabled = link_validation_enabled
        self._backlinks = BacklinkRewriter(crud_repo, link_repo, jobs)

    def _links_validated(self, ws_name: str, owner_id: str) -> bool:
        return self._link_validation_enabled is None or self._link_validation_enabled(
            ws_name, owner_id
        )

    def for_workspace(
        self, ws_name: str, owner_id: str, extra: Iterable[IndexedNote] = ()
    ) -> WorkspaceLinks:
        return self._build(
            ws_name, owner_id, tuple(chain(self._crud_repo.list_paths(ws_name, owner_id), extra))
        )

    def _build(self, ws_name: str, owner_id: str, paths: tuple[IndexedNote, ...]) -> WorkspaceLinks:
        return WorkspaceLinks(self, ws_name, owner_id, paths, LinkIndex(paths))

    def _resolve_links(
        self, workspace: WorkspaceLinks, content: str, source_folder: str
    ) -> LinkResolution:
        resolution = resolve_content_links(workspace.index, content, source_folder)
        if not resolution.xws_ids:
            return resolution
        xws_found = {
            note.id for note in self._crud_repo.get_many(resolution.xws_ids, workspace.owner_id)
        }
        return replace(resolution, resolved_ids=resolution.resolved_ids | xws_found)

    def _validate_wikilinks(
        self, workspace: WorkspaceLinks, content: str, source_folder: str
    ) -> LinkResolution:
        resolution = self._resolve_links(workspace, content, source_folder)
        if resolution.broken and self._links_validated(workspace.ws_name, workspace.owner_id):
            raise BrokenWikilinkError(resolution.broken)
        return resolution

    def persist(self, note_id: str, ws_name: str, owner_id: str, links: LinkResolution) -> None:
        self.persist_many(ws_name, owner_id, {note_id: links})

    def persist_many(
        self,
        ws_name: str,
        owner_id: str,
        resolutions: dict[str, LinkResolution],
        clear_source_ids: set[str] | None = None,
    ) -> None:
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
                        session, source_id, ws_name, owner_id, links.broken_pairs, now=now
                    )
            for source_id in clear_source_ids - resolutions.keys():
                self._link_repo.delete_links_from_in_session(session, source_id)
                if self._dangling_repo is not None:
                    self._dangling_repo.delete_for_source_in_session(session, source_id)
            session.commit()

    def delete_dangling_for_source_in_session(self, session: Session, note_id: str) -> None:
        if self._dangling_repo is not None:
            self._dangling_repo.delete_for_source_in_session(session, note_id)

    def delete_dangling_for_workspace_in_session(
        self, session: Session, ws_name: str, owner_id: str
    ) -> None:
        if self._dangling_repo is not None:
            self._dangling_repo.delete_for_workspace_in_session(session, owner_id, ws_name)

    def _affected_sources(
        self, workspace: WorkspaceLinks, titles: set[str], include_source_ids: Iterable[str]
    ) -> set[str]:
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
        self, target: NoteTarget, include_meta: bool = False, include_cross_workspace: bool = True
    ) -> list[dict]:
        owner_id = target.workspace.owner_id
        same_ws: str | None = None
        if not include_cross_workspace:
            note = self._crud_repo.get(target.note_id, owner_id=owner_id)
            same_ws = note.workspace if note is not None else None
        return resolve_link_notes(
            self._crud_repo,
            self._link_repo.backlinks(target.note_id, same_workspace=same_ws),
            owner_id,
            include_meta,
        )

    def outlinks(self, target: NoteTarget, include_meta: bool = False) -> list[dict]:
        return resolve_link_notes(
            self._crud_repo,
            self._link_repo.outlinks(target.note_id),
            target.workspace.owner_id,
            include_meta,
        )

    def links(
        self, target: NoteTarget, include_meta: bool = False, include_cross_workspace: bool = True
    ) -> dict | None:
        if self._crud_repo.get(target.note_id, owner_id=target.workspace.owner_id) is None:
            return None
        return {
            "backlinks": self.backlinks(target, include_meta, include_cross_workspace),
            "outlinks": self.outlinks(target, include_meta),
        }

    def xws_link_resolver(self, owner_id: str):
        def resolve(note_id: str) -> tuple[str, str] | None:
            note = self._crud_repo.get(note_id, owner_id=owner_id)
            return (
                None
                if note is None
                else (note.title, note_explorer_url(note.workspace, note.folder, note.id))
            )

        return resolve

    def link_resolver(self, workspace: WorkspaceTarget, source_folder: str = "") -> LinkResolver:
        resolver: LinkResolver | None = None

        def resolve(link_target: str):
            nonlocal resolver
            if resolver is None:
                resolver = self.for_workspace(workspace.name, workspace.owner_id).resolver(
                    source_folder
                )
            return resolver(link_target)

        return resolve
