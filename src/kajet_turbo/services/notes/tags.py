from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.log import logger
from kajet_turbo.markdown import (
    extract_inline_tags,
    normalize,
    remap_path,
    rewrite_inline_tags,
)
from kajet_turbo.models import Note
from kajet_turbo.repositories.git import (
    GitError,
    GitRepository,
    defer_workspace_postprocess,
    workspace_write_transaction,
)
from kajet_turbo.repositories.notes import NoteRepository, NoteTagRepository
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.notes.staleness import current_head_sha, sha_is_fresh, stale_payload
from kajet_turbo.workspace import note_filepath, read_note_file, write_note_file

type TaggedPairs = list[tuple[str, str]]


@dataclass(slots=True)
class _RenamedNote:
    """One note staged for a tag rename: what to write, and what to put back if git fails."""

    note: Note
    filepath: str
    relative: str
    new_tags: list[str]
    new_body: str
    old_tags: list[str]
    old_body: str

    @property
    def body_changed(self) -> bool:
        """True when the rename reached inline ``#hashtags``, so the chunks are stale."""
        return self.new_body != self.old_body

    def apply(self, now: str) -> None:
        write_note_file(
            self.filepath,
            self.note.id,
            self.note.title,
            self.new_tags,
            self.note.created_at,
            now,
            self.new_body,
        )

    def restore(self) -> None:
        """Put the note back byte-for-byte, including its original ``updated_at``."""
        write_note_file(
            self.filepath,
            self.note.id,
            self.note.title,
            self.old_tags,
            self.note.created_at,
            self.note.updated_at,
            self.old_body,
        )


class NoteTagService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        tag_repo: NoteTagRepository,
        cache: WorkspaceCache | None,
        indexer: NoteIndexer | None = None,
    ):
        self._crud_repo = crud_repo
        self._tag_repo = tag_repo
        self._cache = cache
        # A tag lives in two places — frontmatter and inline #hashtags — so a rename has to
        # reach into note bodies, and the notes it rewrites need rechunking.
        self._indexer = indexer

    @staticmethod
    def normalize_tags(raw: list[str]) -> list[str]:
        """Normalize frontmatter tags, dropping invalids and duplicates (order kept)."""
        out: list[str] = []
        seen: set[str] = set()
        for tag in raw:
            norm = normalize(tag)
            if norm and norm not in seen:
                seen.add(norm)
                out.append(norm)
        return out

    @staticmethod
    def normalize_with_warnings(
        raw: list[str],
    ) -> tuple[list[str], list[str]]:
        """Like ``normalize_tags`` but returns (normalized_unique, warnings).

        Invalid entries are reported as warnings instead of being silently dropped,
        so a batch tool can surface them without failing the whole call.
        """
        out: list[str] = []
        seen: set[str] = set()
        warnings: list[str] = []
        for tag in raw:
            norm = normalize(tag)
            if norm is None:
                warnings.append(f"{tag!r}: niepoprawny tag — pominięty")
                continue
            if norm not in seen:
                seen.add(norm)
                out.append(norm)
        return out, warnings

    @staticmethod
    def _tagged(fm_tags: list[str], content: str) -> TaggedPairs:
        """Effective ``(path, source)`` pairs: frontmatter wins over the same inline tag."""
        tagged: dict[str, str] = dict.fromkeys(fm_tags, "frontmatter")
        for tag in extract_inline_tags(content):
            tagged.setdefault(tag, "inline")
        return list(tagged.items())

    def sync_tags(
        self, note_id: str, ws_name: str, owner_id: str, fm_tags: list[str], content: str
    ) -> None:
        """Index the note's tags: union of frontmatter (normalized) and inline, frontmatter wins."""
        self._tag_repo.sync_note_tags(note_id, ws_name, owner_id, self._tagged(fm_tags, content))

    @workspace_write_transaction
    def _apply_tag_change(
        self,
        note_id: str,
        owner_id: str,
        ws_path: str,
        mutate: Callable[[list[str], str], tuple[list[str], list[str]]],
    ) -> dict:
        """Read the note's frontmatter tags, apply ``mutate`` -> (new_tags, warnings),
        and persist only if the list changed. Returns the effective state.

        The file (not the DB column) is the source of truth for the current list, so the
        change is computed against on-disk reality. Content/title are never touched.
        """
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        if not Path(filepath).exists():
            raise FileNotFoundError(f"Plik notatki {note_id} nie znaleziony.")
        data = read_note_file(filepath)
        content = data["content"]
        current = NoteTagService.normalize_tags(data["tags"])
        new_tags, warnings = mutate(current, content)
        changed = new_tags != current
        if changed:
            now = datetime.now(UTC).isoformat()
            relative = str(Path(filepath).relative_to(ws_path))
            repo = GitRepository(ws_path)
            try:
                write_note_file(
                    filepath, note_id, note.title, new_tags, note.created_at, now, content
                )
                repo.commit_file(relative, f"note: tag {note.title}")
            except GitError:
                write_note_file(
                    filepath,
                    note_id,
                    note.title,
                    current,
                    note.created_at,
                    note.updated_at,
                    content,
                )
                raise
            self._crud_repo.update(
                note_id,
                owner_id=owner_id,
                title=note.title,
                content=content,
                tags=new_tags,
                updated_at=now,
                folder=note.folder,
            )
            self.sync_tags(note_id, note.workspace, owner_id, new_tags, content)
            if self._cache is not None:
                self._cache.bump(note.workspace, owner_id)
            logger.info("note_tags_changed", note_id=note_id)
        inline = extract_inline_tags(content)
        effective = list(dict.fromkeys([*new_tags, *sorted(inline)]))
        return {
            "note_id": note_id,
            "tags": effective,
            "frontmatter_tags": new_tags,
            "warnings": warnings,
            "changed": changed,
        }

    def add_tags(self, note_id: str, owner_id: str, ws_path: str, tags: list[str]) -> dict:
        """Union ``tags`` into the note's frontmatter list (idempotent, order-preserving)."""

        def mutate(current: list[str], content: str) -> tuple[list[str], list[str]]:
            normalized, warnings = NoteTagService.normalize_with_warnings(tags)
            new_tags = list(dict.fromkeys([*current, *normalized]))
            return new_tags, warnings

        return self._apply_tag_change(note_id, owner_id, ws_path, mutate)

    def remove_tags(self, note_id: str, owner_id: str, ws_path: str, tags: list[str]) -> dict:
        """Remove ``tags`` from the note's frontmatter list (idempotent).

        A requested tag that exists only as an inline ``#hashtag`` in the body cannot be
        removed here (that would mean editing prose); it is reported as a warning instead.
        """

        def mutate(current: list[str], content: str) -> tuple[list[str], list[str]]:
            normalized, warnings = NoteTagService.normalize_with_warnings(tags)
            to_remove = set(normalized)
            new_tags = [t for t in current if t not in to_remove]
            inline = extract_inline_tags(content)
            for tag in normalized:
                if tag in inline:
                    warnings.append(
                        f"{tag}: nadal obecny jako #{tag} w treści — "
                        "usuń edytując body przez edit_note"
                    )
            return new_tags, warnings

        return self._apply_tag_change(note_id, owner_id, ws_path, mutate)

    @workspace_write_transaction
    def set_tags(
        self,
        note_id: str,
        owner_id: str,
        ws_path: str,
        tags: list[str],
        expected_sha: str | None = None,
    ) -> dict:
        """Replace the note's frontmatter tag list.

        Destructive (may drop tags); gated by expected_sha — proof the caller
        read the current version. ``None`` (REST API) skips the check.
        """
        normalized, warnings = NoteTagService.normalize_with_warnings(tags)
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        if not Path(filepath).exists():
            raise FileNotFoundError(f"Plik notatki {note_id} nie znaleziony.")
        if expected_sha is not None:
            relative = str(Path(filepath).relative_to(ws_path))
            if not sha_is_fresh(current_head_sha(ws_path, relative), expected_sha):
                return stale_payload(note_id)
        return self._apply_tag_change(
            note_id, owner_id, ws_path, lambda current, content: (normalized, warnings)
        )

    @workspace_write_transaction
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
        """Rename a tag across the whole workspace in one commit; merges when ``new`` exists.

        The subtree moves with the tag (``work`` -> ``job`` also remaps ``work/projects``),
        matched on segment boundaries so ``workflow`` is left alone. Inline ``#hashtags`` in
        the body are rewritten too — without that, ``sync_tags`` would union the old tag back
        in from the content on the very next sync.

        Merging is destructive in the sense that the two tags become indistinguishable, so it
        needs ``merge=True``; otherwise an existing target is reported as a conflict payload.
        Deliberately unguarded by ``expected_sha`` — like ``move_folder``, this is a
        workspace-wide operation where a per-note sha is impractical; git history is the undo.

        The ``tags`` / ``note_tags`` rows are never touched by hand: the repository rebuilds
        each note's links from scratch and its GC sweeps the tag left orphaned by the rename.
        """
        old_n = normalize(old)
        new_n = normalize(new)
        if old_n is None:
            raise ValueError(f"{old!r}: niepoprawny tag.")
        if new_n is None:
            raise ValueError(f"{new!r}: niepoprawny tag.")
        if old_n == new_n:
            return self._rename_result(old_n, new_n, 0, 0, False, [])
        if new_n.startswith(old_n + "/"):
            raise ValueError(f"Nie można przenieść taga '{old_n}' do jego własnego poddrzewa.")

        source_ids = self._tag_repo.note_ids_for_tags(ws_name, owner_id, [old_n])
        if not source_ids:
            raise ValueError(f"Tag '{old_n}' nie istnieje.")
        target_ids = self._tag_repo.note_ids_for_tags(ws_name, owner_id, [new_n])
        if target_ids and not merge:
            return {
                "error": f"Tag '{new_n}' już istnieje — powtórz z merge=true, żeby scalić.",
                "target": new_n,
                "target_notes": len(target_ids),
                "source_notes": len(source_ids),
            }

        def remap(tag: str) -> str | None:
            return remap_path(tag, old_n, new_n)

        warnings: list[str] = []
        staged: list[_RenamedNote] = []
        for note in self._crud_repo.get_many(sorted(source_ids), owner_id):
            filepath = note_filepath(ws_path, note.folder, note.title)
            if not Path(filepath).exists():
                warnings.append(f"{note.title}: plik notatki nie istnieje — pominięta")
                continue
            data = read_note_file(filepath)
            old_tags = NoteTagService.normalize_tags(data["tags"])
            new_tags = list(dict.fromkeys(remap(t) or t for t in old_tags))
            new_body, _ = rewrite_inline_tags(data["content"], remap)
            if new_tags == old_tags and new_body == data["content"]:
                continue
            staged.append(
                _RenamedNote(
                    note=note,
                    filepath=filepath,
                    relative=str(Path(filepath).relative_to(ws_path)),
                    new_tags=new_tags,
                    new_body=new_body,
                    old_tags=old_tags,
                    old_body=data["content"],
                )
            )
        if not staged:
            return self._rename_result(old_n, new_n, 0, 0, bool(target_ids), warnings)

        now = datetime.now(UTC).isoformat()
        # The writes are inside the guard, not just the commit: a write failing partway
        # through would otherwise leave the workspace half-renamed and diverged from HEAD,
        # and reads go to the files, so they would serve that state.
        written: list[_RenamedNote] = []
        try:
            for item in staged:
                # Recorded before the write: a half-written file needs restoring too.
                written.append(item)
                item.apply(now)
            GitRepository(ws_path).commit_files(
                [item.relative for item in staged], f"tag: rename {old_n} -> {new_n}"
            )
        except GitError, OSError:
            for item in written:
                item.restore()
            raise

        for item in staged:
            self._crud_repo.update(
                item.note.id,
                owner_id=owner_id,
                title=item.note.title,
                tags=item.new_tags,
                updated_at=now,
                folder=item.note.folder,
                bump_index_generation=item.body_changed,
            )
        # One transaction and one orphan sweep for the batch, not one per note.
        self._tag_repo.sync_note_tags_many(
            ws_name,
            owner_id,
            {item.note.id: self._tagged(item.new_tags, item.new_body) for item in staged},
        )
        if self._cache is not None:
            self._cache.bump(ws_name, owner_id)
        rewritten = [item for item in staged if item.body_changed]
        # Chunks are title + content, so only a rewritten body invalidates the search index.
        if self._indexer is not None and rewritten:
            index_payload = [
                {
                    "id": item.note.id,
                    "title": item.note.title,
                    "content": item.new_body,
                    "index_generation": item.note.index_generation + 1,
                }
                for item in rewritten
            ]
            defer_workspace_postprocess(
                ws_path, partial(self._indexer.index_many, ws_name, owner_id, index_payload)
            )
        logger.info(
            "tag_renamed",
            old=old_n,
            new=new_n,
            renamed=len(staged),
            merged=bool(target_ids),
            inline_rewritten=len(rewritten),
        )
        return self._rename_result(
            old_n, new_n, len(staged), len(rewritten), bool(target_ids), warnings
        )

    @staticmethod
    def _rename_result(
        old: str,
        new: str,
        renamed: int,
        inline_rewritten: int,
        merged: bool,
        warnings: list[str],
    ) -> dict:
        return {
            "old": old,
            "new": new,
            "renamed": renamed,
            "merged": merged,
            "inline_rewritten": inline_rewritten,
            "warnings": warnings,
        }
