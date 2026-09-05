from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial

from sqlmodel import Session

from kajet_turbo.log import logger
from kajet_turbo.markdown import (
    extract_inline_tags,
    normalize,
)
from kajet_turbo.repositories.git import (
    GitRepository,
    target_write_transaction,
)
from kajet_turbo.repositories.notes import NoteRepository, NoteTagRepository
from kajet_turbo.services.notes.staged_change import (
    StagedChange,
    commit_rows_then_tree,
)
from kajet_turbo.services.notes.staleness import current_head_sha, sha_is_fresh, stale_payload
from kajet_turbo.services.targets import NoteTarget
from kajet_turbo.workspace import (
    locate_note,
    read_note_file,
    temporal_drop_warnings,
    write_note_file,
)

type TaggedPairs = list[tuple[str, str]]


class NoteTagService:
    def __init__(self, crud_repo: NoteRepository, tag_repo: NoteTagRepository) -> None:
        self._crud_repo = crud_repo
        self._tag_repo = tag_repo

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

    @target_write_transaction
    def _apply_tag_change(
        self,
        target: NoteTarget,
        mutate: Callable[[list[str], str], tuple[list[str], list[str]]],
    ) -> dict:
        """Read the note's frontmatter tags, apply ``mutate`` -> (new_tags, warnings),
        and persist only if the list changed. Returns the effective state.

        The file (not the DB column) is the source of truth for the current list, so the
        change is computed against on-disk reality. Content/title are never touched.
        """
        note_id = target.note_id
        owner_id = target.workspace.owner_id
        ws_path = str(target.workspace.path)
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Note not found: note_id={note_id}")
        loc = locate_note(note, ws_path)
        if not loc.file_exists:
            raise FileNotFoundError(f"Note file not found: note_id={note_id}")
        existing_meta, content = read_note_file(loc.filepath)
        current = NoteTagService.normalize_tags(existing_meta.tags)
        new_tags, warnings = mutate(current, content)
        changed = new_tags != current
        if changed:
            now = datetime.now(UTC).isoformat()
            # A field read_note_file had to drop as unparseable falls back to the DB's
            # last-known-good value instead of the file's (now None) one, so a tag-only
            # edit never silently persists the drop into file and DB (#132 follow-up).
            safe_occurred_at = (
                existing_meta.occurred_at
                if "occurred_at" not in existing_meta.temporal_dropped
                else note.occurred_at
            )
            safe_period = (
                existing_meta.period
                if "period" not in existing_meta.temporal_dropped
                else note.period
            )
            apply_meta = replace(
                existing_meta,
                id=note_id,
                title=note.title,
                tags=new_tags,
                created_at=note.created_at,
                updated_at=now,
                occurred_at=safe_occurred_at,
                period=safe_period,
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
                    occurred_at=safe_occurred_at,
                    period=safe_period,
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
        # TagOperationResult.warnings is untyped list[str] (unlike update()/edit_many()'s
        # typed TemporalWarning), so this formats its own English text from the same
        # {kind, field} payload rather than workspace.py growing a second shape for it.
        temporal_warnings = [
            f"{w['field']}: file value was unparseable — ignored, kept the previous value."
            for w in temporal_drop_warnings(existing_meta.temporal_dropped)
        ]
        return {
            "note_id": note_id,
            "tags": effective,
            "frontmatter_tags": new_tags,
            "warnings": [*warnings, *temporal_warnings],
            "changed": changed,
        }

    def add_tags(self, target: NoteTarget, tags: list[str]) -> dict:
        """Union ``tags`` into the note's frontmatter list (idempotent, order-preserving)."""

        def mutate(current: list[str], content: str) -> tuple[list[str], list[str]]:
            normalized, warnings = NoteTagService.normalize_with_warnings(tags)
            new_tags = list(dict.fromkeys([*current, *normalized]))
            return new_tags, warnings

        return self._apply_tag_change(target, mutate)

    def remove_tags(self, target: NoteTarget, tags: list[str]) -> dict:
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
                        f"{tag}: still present as #{tag} in the body — "
                        "remove it by editing the body via edit_note"
                    )
            return new_tags, warnings

        return self._apply_tag_change(target, mutate)

    @target_write_transaction
    def set_tags(
        self,
        target: NoteTarget,
        tags: list[str],
        expected_sha: str | None = None,
    ) -> dict:
        """Replace the note's frontmatter tag list.

        Destructive (may drop tags); gated by expected_sha — proof the caller
        read the current version. ``None`` (REST API) skips the check.
        """
        note_id = target.note_id
        owner_id = target.workspace.owner_id
        ws_path = str(target.workspace.path)
        normalized, warnings = NoteTagService.normalize_with_warnings(tags)
        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Note not found: note_id={note_id}")
        loc = locate_note(note, ws_path)
        if not loc.file_exists:
            raise FileNotFoundError(f"Note file not found: note_id={note_id}")
        if expected_sha is not None and not sha_is_fresh(
            current_head_sha(ws_path, loc.relative), expected_sha
        ):
            return stale_payload(note_id)
        return self._apply_tag_change(target, lambda current, content: (normalized, warnings))
