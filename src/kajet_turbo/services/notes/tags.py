from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from kajet_turbo.cache import WorkspaceCache
from kajet_turbo.log import logger
from kajet_turbo.markdown import extract_inline_tags, normalize
from kajet_turbo.repositories.git import GitError, GitRepository
from kajet_turbo.repositories.notes import NoteRepository, NoteTagRepository
from kajet_turbo.workspace import note_filepath, read_note_file, write_note_file

type TaggedPairs = list[tuple[str, str]]


class NoteTagService:
    def __init__(
        self,
        crud_repo: NoteRepository,
        tag_repo: NoteTagRepository,
        cache: WorkspaceCache | None,
    ):
        self._crud_repo = crud_repo
        self._tag_repo = tag_repo
        self._cache = cache

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

    def sync_tags(
        self, note_id: str, ws_name: str, owner_id: str, fm_tags: list[str], content: str
    ) -> None:
        """Index the note's tags: union of frontmatter (normalized) and inline, frontmatter wins."""
        tagged: dict[str, str] = dict.fromkeys(fm_tags, "frontmatter")
        for tag in extract_inline_tags(content):
            tagged.setdefault(tag, "inline")
        self._tag_repo.sync_note_tags(note_id, ws_name, owner_id, list(tagged.items()))

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
        if new_tags != current:
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

    def set_tags(
        self,
        note_id: str,
        owner_id: str,
        ws_path: str,
        tags: list[str],
        confirm: bool = False,
    ) -> dict:
        """Replace the note's frontmatter tag list.

        If tags would be removed and confirm is False, returns a requires_confirmation
        payload instead of writing. The _ConfirmationRequired exception-as-control-flow
        from the old implementation is replaced with an explicit early-return pattern.
        """
        normalized, warnings = NoteTagService.normalize_with_warnings(tags)
        new_set = set(normalized)

        def mutate(current: list[str], content: str) -> tuple[list[str] | None, list[str]]:
            would_remove = [t for t in current if t not in new_set]
            if would_remove and not confirm:
                return None, would_remove  # signal: early return needed
            return normalized, warnings

        note = self._crud_repo.get(note_id, owner_id=owner_id)
        if note is None:
            raise ValueError(f"Notatka {note_id} nie znaleziona.")
        filepath = note_filepath(ws_path, note.folder, note.title)
        if not Path(filepath).exists():
            raise FileNotFoundError(f"Plik notatki {note_id} nie znaleziony.")
        data = read_note_file(filepath)
        current = NoteTagService.normalize_tags(data["tags"])
        new_tags, extra = mutate(current, data["content"])
        if new_tags is None:
            # extra is the would_remove list
            return NoteTagService._confirmation_payload(note_id, extra, False)
        return self._apply_tag_change(note_id, owner_id, ws_path, lambda c, _: (new_tags, warnings))

    @staticmethod
    def _confirmation_payload(
        note_id: str,
        would_remove: list[str],
        overwrites_content: bool,
    ) -> dict:
        """Non-applied result telling the caller a destructive op needs confirmation."""
        parts: list[str] = []
        if would_remove:
            parts.append(f"usunie tagi: {', '.join(would_remove)}")
        if overwrites_content:
            parts.append("nadpisze istniejącą treść notatki")
        return {
            "note_id": note_id,
            "requires_confirmation": True,
            "would_remove_tags": would_remove,
            "overwrites_content": overwrites_content,
            "warning": (
                "Operacja destrukcyjna: "
                + "; ".join(parts)
                + ". Potwierdź z użytkownikiem i zawołaj ponownie z confirm=true."
            ),
        }
