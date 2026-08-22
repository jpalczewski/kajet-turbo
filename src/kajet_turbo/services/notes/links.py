import json
from collections.abc import Callable, Iterable
from dataclasses import replace
from functools import cache, partial
from itertools import chain
from pathlib import Path

from kajet_turbo.log import logger
from kajet_turbo.markdown import (
    BrokenWikilinkError,
    IndexedNote,
    LinkIndex,
    LinkResolution,
    LinkResolver,
    extract_wikilinks,
    note_explorer_url,
    resolve_content_links,
    rewrite_wikilinks,
)
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import NoteLinkRepository, NoteRepository
from kajet_turbo.workspace import note_filepath, path_segments, read_note_file, write_note_file

# (old, new) identity of a note that was moved and/or renamed.
type NoteMove = tuple[IndexedNote, IndexedNote]


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
            # Link-free content (the common case) must not pay for the workspace index.
            if not extract_wikilinks(content):
                return LinkResolution(set(), [], [])
            index = self.link_index(ws_name, owner_id)
        resolution = resolve_content_links(index, content, source_folder)
        if not resolution.xws_ids:
            return resolution
        xws_found = {n.id for n in self._crud_repo.get_many(resolution.xws_ids, owner_id)}
        return replace(resolution, resolved_ids=resolution.resolved_ids | xws_found)

    def validate_wikilinks(
        self,
        ws_name: str,
        owner_id: str,
        content: str,
        source_folder: str,
        index: LinkIndex | None = None,
    ) -> LinkResolution:
        """``resolve_links`` plus the workspace's validation policy: with validation on, any
        broken intra-workspace target raises ``BrokenWikilinkError``; with it off, the
        broken targets stay in the result for the dangling-link table."""
        resolution = self.resolve_links(ws_name, owner_id, content, source_folder, index)
        if resolution.broken and self._links_validated(ws_name, owner_id):
            raise BrokenWikilinkError(resolution.broken)
        return resolution

    def persist(self, note_id: str, ws_name: str, owner_id: str, links: LinkResolution) -> None:
        """Store one note's resolution outcome: the link-graph edges for what resolved and
        the dangling rows for what did not — both halves, so no write path can drift."""
        self._link_repo.replace_links(note_id, ws_name, owner_id, links.resolved_ids)
        self.write_dangling(note_id, ws_name, owner_id, links.broken_pairs)

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

    def delete_dangling_for_workspace(self, ws_name: str, owner_id: str) -> None:
        """Remove every dangling link row of a workspace. No-op when not wired."""
        if self._dangling_repo is not None:
            self._dangling_repo.delete_for_workspace(owner_id, ws_name)

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
        """Render-time resolver with the same rules as validation, ranked from the rendered
        note's own folder. The index is loaded on the first link, so link-free notes cost
        no query."""

        @cache
        def index() -> LinkIndex:
            return self.link_index(ws_name, owner_id)

        return lambda target: index().resolve(target, source_folder)

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
        self, moves: list[NoteMove], owner_id: str, ws_path: str, ws_name: str
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

        Each affected source is committed separately, via a raw write — deliberately not the
        full ``NoteService.update()`` pipeline. Four steps ``update()`` runs are skipped here,
        each for its own reason:

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
        source_ids = self._link_repo.backlinks_many(list(moved), same_workspace=ws_name)
        if not source_ids:
            return
        paths = self._crud_repo.list_paths(ws_name, owner_id)
        after = LinkIndex(paths)
        before = LinkIndex(
            chain((p for p in paths if p.note_id not in moved), (old for old, _ in moves))
        )
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
        repo = GitRepository(ws_path)
        rewritten = 0
        for src in self._crud_repo.get_many(sorted(source_ids), owner_id):
            src_path = note_filepath(ws_path, src.folder, src.title)
            if not Path(src_path).exists():
                continue
            data = read_note_file(src_path)
            # A source that moved along with its targets (folder move) must be ranked from
            # where it *was* when its links were written, not from its new folder.
            new_body, changed = rewrite_wikilinks(
                data["content"],
                partial(
                    rewrite,
                    before_folder=old_folders.get(src.id, src.folder),
                    after_folder=src.folder,
                ),
            )
            if not changed:
                continue
            rewritten += 1
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
            repo.commit_file(relative, message)
            self._crud_repo.update(
                src.id, owner_id=owner_id, content=new_body, updated_at=src.updated_at
            )
        logger.info(
            "backlinks_rewritten",
            ws=ws_name,
            moved=len(moves),
            sources=len(source_ids),
            rewritten=rewritten,
        )
