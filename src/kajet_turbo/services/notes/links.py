import json
from collections.abc import Callable, Iterable
from itertools import chain
from pathlib import Path
from urllib.parse import quote

from kajet_turbo.markdown import (
    BrokenWikilinkError,
    IndexedNote,
    LinkIndex,
    LinkResolution,
    LinkResolver,
    TargetRewriter,
    join_target,
    resolve_content_links,
    rewrite_wikilinks,
)
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import NoteLinkRepository, NoteRepository
from kajet_turbo.workspace import note_filepath, read_note_file, write_note_file

type BrokenPairs = list[tuple[str, str]]
type ResolvedIds = set[str]


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

    def link_index(
        self, ws_name: str, owner_id: str, extra: Iterable[IndexedNote] = ()
    ) -> LinkIndex:
        """Snapshot of the workspace's notes for wikilink resolution. ``extra`` adds notes
        that don't exist in the DB yet (a batch being saved) so in-batch links resolve."""
        return LinkIndex(chain(self._crud_repo.list_paths(ws_name, owner_id), extra))

    def resolve_links(
        self,
        ws_name: str,
        owner_id: str,
        content: str,
        source_folder: str,
        index: LinkIndex | None = None,
    ) -> LinkResolution:
        """Resolve every wikilink in ``content`` without judging the result.

        Intra-workspace targets resolve against ``index`` (built on demand when omitted;
        pass one to share it across a batch). ``[[note:ID]]`` cross-workspace links are
        resolved by note ID and folded into ``resolved_ids`` — a missing ID is simply
        dropped, never reported as broken (there is no dangling row to write for it).
        """
        if index is None:
            index = self.link_index(ws_name, owner_id)
        resolution = resolve_content_links(index, content, source_folder)
        xws_found = {
            note_id
            for note_id in resolution.xws_ids
            if self._crud_repo.get(note_id, owner_id=owner_id) is not None
        }
        return LinkResolution(
            resolution.resolved_ids | xws_found, resolution.broken, resolution.xws_ids
        )

    def validate_wikilinks(
        self,
        ws_name: str,
        owner_id: str,
        content: str,
        source_folder: str,
        index: LinkIndex | None = None,
    ) -> tuple[ResolvedIds, BrokenPairs]:
        """``resolve_links`` plus the workspace's validation policy: with validation on, any
        broken intra-workspace target raises ``BrokenWikilinkError``; with it off, broken
        targets come back as ``(folder, title)`` pairs for the dangling-link table."""
        resolution = self.resolve_links(ws_name, owner_id, content, source_folder, index)
        if resolution.broken and self._links_validated(ws_name, owner_id):
            raise BrokenWikilinkError(resolution.broken)
        return resolution.resolved_ids, resolution.broken_pairs

    def write_dangling(
        self,
        source_note_id: str,
        ws_name: str,
        owner_id: str,
        broken_pairs: list[tuple[str, str]],
    ) -> None:
        """Persist (or clear) the source note's dangling links. No-op when not wired."""
        if self._dangling_repo is None:
            return
        self._dangling_repo.replace_for_source(source_note_id, ws_name, owner_id, broken_pairs)

    def delete_dangling_for_source(self, note_id: str) -> None:
        """Remove dangling link rows for a deleted source note. No-op when not wired."""
        if self._dangling_repo is not None:
            self._dangling_repo.delete_for_source(note_id)

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

    def link_resolver(self, ws_name: str, owner_id: str, source_folder: str = "") -> LinkResolver:
        """Render-time resolver: one index snapshot per rendered note, the same resolution
        rules as validation, ranked from the rendered note's own folder."""
        index = self.link_index(ws_name, owner_id)
        return lambda target: index.resolve(target, source_folder)

    def xws_link_resolver(self, owner_id: str):
        def resolve(note_id: str) -> tuple[str, str] | None:
            note = self._crud_repo.get(note_id, owner_id=owner_id)
            if note is None:
                return None
            segments = [quote(s) for s in note.folder.split("/") if s] + [note.id]
            url = f"/workspace/{note.workspace}/notes/{'/'.join(segments)}"
            return note.title, url

        return resolve

    def _resolve_link_notes(
        self,
        note_ids: list[str],
        owner_id: str,
        include_meta: bool = False,
    ) -> list[dict]:
        """Map note_ids to ``{note_id, title, folder, workspace}``, skipping missing notes.
        With ``include_meta=True`` also includes ``tags`` and ``updated_at``."""
        result = []
        for note_id in note_ids:
            note = self._crud_repo.get(note_id, owner_id=owner_id)
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

    def rewrite_backlinks(
        self,
        note_id: str,
        owner_id: str,
        ws_path: str,
        ws_name: str,
        old_folder: str,
        old_title: str,
        new_folder: str,
        new_title: str,
    ) -> None:
        """Rewrite wikilink paths in every note that links to ``note_id`` after it moved/renamed.

        Only same-workspace backlinks are rewritten: cross-workspace links use ``[[note:ID]]``
        syntax which is ID-stable and needs no path update.

        Each affected source is committed separately, via a raw write — deliberately not the
        full ``NoteService.update()`` pipeline. Four steps ``update()`` runs are skipped here,
        each for its own reason:

        - ``replace_links``: no-op by construction. A link's graph edge is keyed on
          ``(source_note_id, target_note_id)``; rewriting the path text doesn't change the
          target's identity, so the edge is already correct.
        - ``sync_tags``: the rewrite only touches wikilink path text, never frontmatter tags.
        - ``write_dangling``: this only rewrites links that already resolved (they're in the
          link graph in the first place), so no pair moves between resolved/dangling.
        - Search reindexing (chunks/FTS/embeddings): genuinely skipped, not a no-op. This
          method has no indexer reference, so a rewritten source note's search index keeps
          the OLD link text until that note's next real edit or a workspace-wide reindex.
          Accepted as a cosmetic, self-healing gap — not worth threading an indexer through
          this call for a stale snippet/chunk-offset window that closes on the next edit.
        """
        source_ids = self._link_repo.backlinks(note_id, same_workspace=ws_name)
        if not source_ids:
            return
        # The DB already holds the note's new path. Links that pointed at it — including
        # short [[Title]] forms, which carry no path to compare — are found by resolving
        # each source's links against the pre-move index (the current one with this note's
        # entry swapped back), and rewritten to the shortest form that still resolves to it
        # afterwards: a bare title stays bare when it is still unambiguous, anything else
        # gets the full path, which is exact by construction.
        paths = self._crud_repo.list_paths(ws_name, owner_id)
        after = LinkIndex(paths)
        before = LinkIndex(
            IndexedNote(note_id, old_folder, old_title) if p.note_id == note_id else p
            for p in paths
        )
        full_target = join_target(new_folder, new_title)

        def rewriter(source_folder: str) -> TargetRewriter:
            def rewrite(target: str) -> str | None:
                hit = before.resolve(target, source_folder)
                if hit is None or hit.note_id != note_id:
                    return None
                if "/" in target:
                    return full_target
                short = after.resolve(new_title, source_folder)
                return new_title if short is not None and short.note_id == note_id else full_target

            return rewrite

        repo = GitRepository(ws_path)
        for source_id in source_ids:
            src = self._crud_repo.get(source_id, owner_id=owner_id)
            if src is None:
                continue
            src_path = note_filepath(ws_path, src.folder, src.title)
            if not Path(src_path).exists():
                continue
            data = read_note_file(src_path)
            new_body, changed = rewrite_wikilinks(data["content"], rewriter(src.folder))
            if not changed:
                continue
            write_note_file(
                src_path,
                src.id,
                src.title,
                json.loads(src.tags or "[]"),
                src.created_at,
                src.updated_at,
                new_body,
            )
            relative = str(Path(src_path).relative_to(ws_path))
            repo.commit_file(relative, f"note: rewrite wikilink {old_title} -> {new_title}")
            self._crud_repo.update(
                source_id, owner_id=owner_id, content=new_body, updated_at=src.updated_at
            )
