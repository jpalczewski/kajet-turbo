import builtins
import json
from collections.abc import Callable
from pathlib import Path

from kajet_turbo.markdown import (
    BrokenWikilinkError,
    extract_wikilinks,
    rewrite_wikilink_target,
    split_target,
)
from kajet_turbo.repositories.dangling_links import DanglingLinkRepository
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import NoteLinkRepository, NoteRepository
from kajet_turbo.workspace import note_filepath, read_note_file, write_note_file


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

    def validate_wikilinks(
        self,
        ws_name: str,
        owner_id: str,
        content: str,
        extra_targets: dict[tuple[str, str], str] | None = None,
    ) -> tuple[set[str], builtins.list[tuple[str, str]]]:
        """Resolve every wikilink in ``content``. Returns ``(resolved_ids, broken_pairs)``.

        ``extra_targets`` maps ``(folder, title) -> note_id`` for notes created in the
        same batch that are not yet persisted, so in-batch links resolve before insert.
        Raises BrokenWikilinkError when validation is on and any link is broken; with
        validation off, broken (folder, title) pairs are returned for dangling storage.
        """
        pairs = [(target, split_target(target)) for target, _ in extract_wikilinks(content)]
        if not pairs:
            return set(), []
        resolved = self._crud_repo.resolve_paths(ws_name, owner_id, [pair for _, pair in pairs])
        if extra_targets:
            for _, pair in pairs:
                if pair not in resolved and pair in extra_targets:
                    resolved[pair] = extra_targets[pair]
        broken_targets = sorted({target for target, pair in pairs if pair not in resolved})
        if broken_targets and self._links_validated(ws_name, owner_id):
            raise BrokenWikilinkError(broken_targets)
        # Validation off: broken links are dropped (no note_links edge); resolved
        # targets still flow to the graph. Dangling links persist via write_dangling.
        broken_pairs = sorted({pair for _, pair in pairs if pair not in resolved})
        return set(resolved.values()), broken_pairs

    def write_dangling(
        self,
        source_note_id: str,
        ws_name: str,
        owner_id: str,
        broken_pairs: builtins.list[tuple[str, str]],
    ) -> None:
        """Persist (or clear) the source note's dangling links. No-op when not wired."""
        if self._dangling_repo is None:
            return
        self._dangling_repo.replace_for_source(source_note_id, ws_name, owner_id, broken_pairs)

    def delete_dangling_for_source(self, note_id: str) -> None:
        """Remove dangling link rows for a deleted source note. No-op when not wired."""
        if self._dangling_repo is not None:
            self._dangling_repo.delete_for_source(note_id)

    def backlinks(self, note_id: str, owner_id: str, include_meta: bool = False) -> builtins.list[dict]:
        return self._resolve_link_notes(self._link_repo.backlinks(note_id), owner_id, include_meta)

    def outlinks(self, note_id: str, owner_id: str, include_meta: bool = False) -> builtins.list[dict]:
        return self._resolve_link_notes(self._link_repo.outlinks(note_id), owner_id, include_meta)

    def links(self, note_id: str, owner_id: str, include_meta: bool = False) -> dict | None:
        if self._crud_repo.get(note_id, owner_id=owner_id) is None:
            return None
        return {
            "backlinks": self.backlinks(note_id, owner_id, include_meta),
            "outlinks": self.outlinks(note_id, owner_id, include_meta),
        }

    def link_resolver(self, ws_name: str, owner_id: str):
        def resolve(folder: str, title: str) -> str | None:
            note = self._crud_repo.get_by_path(ws_name, owner_id, folder, title)
            return note.id if note else None
        return resolve

    def _resolve_link_notes(
        self,
        note_ids: builtins.list[str],
        owner_id: str,
        include_meta: bool = False,
    ) -> builtins.list[dict]:
        """Map note_ids to ``{note_id, title, folder}``, skipping missing/foreign notes.
        With ``include_meta=True`` also includes ``tags`` and ``updated_at``."""
        result = []
        for note_id in note_ids:
            note = self._crud_repo.get(note_id, owner_id=owner_id)
            if note is None:
                continue
            entry: dict = {"note_id": note.id, "title": note.title, "folder": note.folder}
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
        old_folder: str,
        old_title: str,
        new_folder: str,
        new_title: str,
    ) -> None:
        """Rewrite wikilink paths in every note that links to ``note_id`` after it moved/renamed.

        Each affected source is committed separately; the link graph edges are unchanged
        (``target_note_id`` stays the same), so no link-table update is needed.
        """
        source_ids = self._link_repo.backlinks(note_id)
        if not source_ids:
            return
        old_key = (old_folder, old_title)
        new_target = f"{new_folder}/{new_title}" if new_folder else new_title
        repo = GitRepository(ws_path)
        for source_id in source_ids:
            src = self._crud_repo.get(source_id, owner_id=owner_id)
            if src is None:
                continue
            src_path = note_filepath(ws_path, src.folder, src.title)
            if not Path(src_path).exists():
                continue
            data = read_note_file(src_path)
            new_body, changed = rewrite_wikilink_target(data["content"], old_key, new_target)
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
