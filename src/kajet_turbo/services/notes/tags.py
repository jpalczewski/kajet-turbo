from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from itertools import batched

from sqlmodel import Session

from kajet_turbo.log import logger
from kajet_turbo.markdown import (
    extract_inline_tags,
    normalize,
    remap_path,
    rewrite_inline_tags,
)
from kajet_turbo.repositories.git import (
    GitRepository,
    defer_workspace_postprocess,
    workspace_write_transaction,
)
from kajet_turbo.repositories.notes import NoteRepository, NoteTagRepository
from kajet_turbo.services.indexing import NoteIndexer
from kajet_turbo.services.notes.staged_change import (
    MAX_BATCH_COMMIT_SIZE,
    StagedChange,
    commit_rows_then_tree,
)
from kajet_turbo.services.notes.staleness import current_head_sha, sha_is_fresh, stale_payload
from kajet_turbo.workspace import (
    LocatedNote,
    NoteFrontmatter,
    locate_note,
    read_note_file,
    write_note_file,
)

type TaggedPairs = list[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class _RenamedNote:
    """One note staged for a tag rename: what to write, and what body changed for chunking."""

    loc: LocatedNote
    meta: NoteFrontmatter
    new_tags: list[str]
    new_body: str
    old_body: str

    @property
    def note(self):
        return self.loc.note

    @property
    def body_changed(self) -> bool:
        """True when the rename reached inline ``#hashtags``, so the chunks are stale."""
        return self.new_body != self.old_body


class NoteTagService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        tag_repo: NoteTagRepository,
        indexer: NoteIndexer | None = None,
    ):
        self._crud_repo = crud_repo
        self._tag_repo = tag_repo
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
    def tagged(fm_tags: list[str], content: str) -> TaggedPairs:
        """Effective ``(path, source)`` pairs: frontmatter wins over the same inline tag."""
        tagged: dict[str, str] = dict.fromkeys(fm_tags, "frontmatter")
        for tag in extract_inline_tags(content):
            tagged.setdefault(tag, "inline")
        return list(tagged.items())

    def sync_tags(
        self, note_id: str, ws_name: str, owner_id: str, fm_tags: list[str], content: str
    ) -> None:
        """Index the note's tags: union of frontmatter (normalized) and inline, frontmatter wins."""
        self._tag_repo.sync_note_tags(note_id, ws_name, owner_id, self.tagged(fm_tags, content))

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
        loc = locate_note(note, ws_path)
        if not loc.file_exists:
            raise FileNotFoundError(f"Plik notatki {note_id} nie znaleziony.")
        existing_meta, content = read_note_file(loc.filepath)
        current = NoteTagService.normalize_tags(existing_meta.tags)
        new_tags, warnings = mutate(current, content)
        changed = new_tags != current
        if changed:
            now = datetime.now(UTC).isoformat()
            apply_meta = replace(
                existing_meta,
                id=note_id,
                title=note.title,
                tags=new_tags,
                created_at=note.created_at,
                updated_at=now,
            )
            item = StagedChange(
                add=loc.relative,
                remove=None,
                apply=partial(write_note_file, loc.filepath, apply_meta, content),
            )

            def write_rows(session: Session) -> None:
                self._crud_repo.update_in_session(
                    session,
                    note_id,
                    owner_id=owner_id,
                    title=note.title,
                    tags=new_tags,
                    updated_at=now,
                    folder=note.folder,
                    occurred_at=existing_meta.occurred_at,
                    period=existing_meta.period,
                )

            commit_rows_then_tree(
                self._crud_repo,
                GitRepository(ws_path),
                [item],
                f"note: tag {note.title}",
                operation="update_tags",
                write_rows=write_rows,
                note_id=note_id,
                owner_id=owner_id,
            )
            self.sync_tags(note_id, note.workspace, owner_id, new_tags, content)
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
        loc = locate_note(note, ws_path)
        if not loc.file_exists:
            raise FileNotFoundError(f"Plik notatki {note_id} nie znaleziony.")
        if expected_sha is not None and not sha_is_fresh(
            current_head_sha(ws_path, loc.relative), expected_sha
        ):
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
            loc = locate_note(note, ws_path)
            if not loc.file_exists:
                warnings.append(f"{note.title}: plik notatki nie istnieje — pominięta")
                continue
            existing_meta, content = read_note_file(loc.filepath)
            old_tags = NoteTagService.normalize_tags(existing_meta.tags)
            new_tags = list(dict.fromkeys(remap(t) or t for t in old_tags))
            new_body, _ = rewrite_inline_tags(content, remap)
            if new_tags == old_tags and new_body == content:
                continue
            staged.append(
                _RenamedNote(
                    loc=loc,
                    meta=existing_meta,
                    new_tags=new_tags,
                    new_body=new_body,
                    old_body=content,
                )
            )
        if not staged:
            return self._rename_result(old_n, new_n, 0, 0, bool(target_ids), warnings)

        now = datetime.now(UTC).isoformat()
        git_repo = GitRepository(ws_path)
        message = f"tag: rename {old_n} -> {new_n}"

        # Chunked (#171): one commit_rows_then_tree call per MAX_BATCH_COMMIT_SIZE notes,
        # not one for the whole (workspace-derived, unbounded) rename. sync_note_tags_many
        # runs *inside* the loop — not after it — because note_ids_for_tags (used by a
        # retry after a mid-batch failure) reads the note_tags join table that call
        # maintains, so resumability depends on each chunk's join-table rows being synced
        # before the next chunk or a re-run queries them again.
        rewritten: list[_RenamedNote] = []
        for chunk in batched(staged, MAX_BATCH_COMMIT_SIZE, strict=False):
            items = [
                StagedChange(
                    add=item.loc.relative,
                    remove=None,
                    apply=partial(
                        write_note_file,
                        item.loc.filepath,
                        replace(
                            item.meta,
                            id=item.note.id,
                            title=item.note.title,
                            tags=item.new_tags,
                            created_at=item.note.created_at,
                            updated_at=now,
                        ),
                        item.new_body,
                    ),
                )
                for item in chunk
            ]

            # write_rows runs synchronously within this same iteration (commit_rows_then_tree
            # doesn't defer it), so closing over `chunk` is safe — the default arg below only
            # silences ruff's B023, which can't see that.
            def write_rows(session: Session, chunk: tuple[_RenamedNote, ...] = chunk) -> None:
                for item in chunk:
                    self._crud_repo.update_in_session(
                        session,
                        item.note.id,
                        owner_id=owner_id,
                        title=item.note.title,
                        tags=item.new_tags,
                        updated_at=now,
                        folder=item.note.folder,
                        occurred_at=item.meta.occurred_at,
                        period=item.meta.period,
                        bump_index_generation=item.body_changed,
                    )

            commit_rows_then_tree(
                self._crud_repo,
                git_repo,
                items,
                message,
                operation="rename_tag",
                write_rows=write_rows,
                owner_id=owner_id,
                old=old_n,
                new=new_n,
                count=len(chunk),
                note_ids=[item.note.id for item in chunk],
            )
            self._tag_repo.sync_note_tags_many(
                ws_name,
                owner_id,
                {item.note.id: self.tagged(item.new_tags, item.new_body) for item in chunk},
            )
            chunk_rewritten = [item for item in chunk if item.body_changed]
            rewritten.extend(chunk_rewritten)
            # Chunks are title + content, so only a rewritten body invalidates the search index.
            if self._indexer is not None and chunk_rewritten:
                index_payload = [{"id": item.note.id} for item in chunk_rewritten]
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
