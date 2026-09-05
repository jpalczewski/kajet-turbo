import json
from dataclasses import asdict
from pathlib import Path

import frontmatter

from kajet_turbo.log import logger
from kajet_turbo.markdown import build_outline, join_target, split_target
from kajet_turbo.repositories.git import GitRepository
from kajet_turbo.repositories.notes import (
    NoteRepository,
    NoteTagRepository,
    folder_sort_key,
    note_to_list_item,
)
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.notes.links import NoteLinkService
from kajet_turbo.services.notes.locator import locate_many
from kajet_turbo.services.notes.staleness import current_head_sha
from kajet_turbo.services.notes.types import NoteData
from kajet_turbo.services.targets import NoteTarget, WorkspaceTarget
from kajet_turbo.workspace import (
    LocatedNote,
    iter_note_paths,
    locate_note,
    normalize_folder,
    note_filepath,
    note_folder,
    parse_frontmatter,
    read_note_file,
)


class NoteReadService:
    """General note reads: metadata/content/corpus reads, addressed by id or by
    (folder, title) natural key. No workspace write lock, no note-body writes."""

    def __init__(
        self,
        crud_repo: NoteRepository,
        tag_repo: NoteTagRepository,
        link_service: NoteLinkService,
        indexer: NoteIndexer | None = None,
    ) -> None:
        self._crud_repo = crud_repo
        self._tag_repo = tag_repo
        self._link_service = link_service
        self._indexer = indexer

    def _locate(self, note_id: str, owner_id: str, ws_path: str) -> LocatedNote | None:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        return locate_note(note, ws_path)

    def _note_data(self, loc: LocatedNote, sha: str | None) -> NoteData:
        if sha is None:
            raise ValueError(
                f"Notatka {loc.note.id} nie ma historii commitów (niespójny stan repo)."
            )
        _, content = read_note_file(loc.filepath)
        return NoteData(**note_to_list_item(loc.note), content=content, sha=sha)

    def get(self, note_id: str, owner_id: str) -> dict | None:
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        return note_to_list_item(note) if note is not None else None

    def get_with_content(self, target: NoteTarget) -> NoteData | None:
        """Single-note read. Batch reads go through ``get_many``/``locate_many``
        instead — a shared git walk beats N of these."""
        ws_path = str(target.workspace.path)
        loc = self._locate(target.note_id, target.workspace.owner_id, ws_path)
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
        self, title: str, folder: str | None, target: WorkspaceTarget
    ) -> NoteData | None:
        """Read a note addressed by its ``(folder, title)`` natural key. See
        ``resolve_note_id`` for the path semantics."""
        note_id = self.resolve_note_id(title, folder, target.owner_id, target.name)
        if note_id is None:
            return None
        return self.get_with_content(NoteTarget(note_id=note_id, workspace=target))

    def get_outline(self, target: NoteTarget) -> dict | None:
        """Note structure (headings + section sizes) without content — for picking a
        target_heading before a surgical edit_note call without loading the full body."""
        loc = self._locate(target.note_id, target.workspace.owner_id, str(target.workspace.path))
        if loc is None or not loc.file_exists:
            return None
        note = loc.note
        _, content = read_note_file(loc.filepath)
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

    def get_many(self, targets: list[NoteTarget]) -> list[NoteData | dict]:
        """Read multiple notes in one call. Best-effort per id: a missing note becomes
        {"note_id": ..., "error": ...} instead of failing the whole call. Order-preserving.
        Grouped by workspace so each distinct workspace's git history walk still runs
        once (head_shas_for_paths) rather than once per note — targets may legitimately
        span more than one workspace (see TargetResolver.notes)."""
        by_workspace: dict[str, list[NoteTarget]] = {}
        for target in targets:
            by_workspace.setdefault(str(target.workspace.path), []).append(target)

        located: dict[str, LocatedNote] = {}
        for ws_path, group in by_workspace.items():
            owner_id = group[0].workspace.owner_id
            git_repo = GitRepository(ws_path)
            located.update(
                locate_many(
                    self._crud_repo, [t.note_id for t in group], owner_id, ws_path, git_repo
                )
            )

        results: list[NoteData | dict] = []
        for target in targets:
            loc = located.get(target.note_id.strip())
            if loc is None or not loc.file_exists:
                error = f"Note not found: note_id={target.note_id}"
                results.append({"note_id": target.note_id, "error": error})
            else:
                results.append(self._note_data(loc, loc.head_sha))
        return results

    def preview_chunks(self, note_id: str, owner_id: str, ws_path: str) -> dict | None:
        """Live chunk preview for a note (reads current file content; never stored rows)."""
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            return None
        workspace = WorkspaceTarget(owner_id=owner_id, name=note.workspace, path=Path(ws_path))
        data = self.get_with_content(NoteTarget(note_id=note_id, workspace=workspace))
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
        for relative in iter_note_paths(ws_path):
            filepath = ws_root / relative
            note_dir = note_folder(ws_path, filepath)
            if scope is not None and not (note_dir == scope or note_dir.startswith(scope + "/")):
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
                        "folder": note_dir,
                        "line_number": max(0, raw_line_number - fm_offset),
                        "line": line,
                    }
                )
            if truncated:
                break
        logger.info("notes_grep", ws=ws_name, matches=len(matches), truncated=truncated)
        return {"matches": matches, "truncated": truncated}

    def list_notes(
        self,
        target: WorkspaceTarget,
        tags: list[str] | None = None,
        limit: int | None = 20,
        folder: str | None = None,
        include_descendants: bool = True,
        sort: str = "default",
    ) -> list[dict]:
        ws_name = target.name
        owner_id = target.owner_id
        allowed_note_ids: set[str] | None = None
        if tags:
            allowed_note_ids = self._tag_repo.note_ids_for_tags(
                ws_name,
                owner_id,
                tags,
                include_descendants=include_descendants,
            )
        return self._crud_repo.list_notes(
            ws_name,
            owner_id=owner_id,
            allowed_note_ids=allowed_note_ids,
            limit=limit,
            folder=folder,
            sort=sort,
        )
