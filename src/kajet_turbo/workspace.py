import os
import re
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import cast

import frontmatter

from kajet_turbo.log import logger
from kajet_turbo.models import Note
from kajet_turbo.periods import parse_period_key
from kajet_turbo.repositories.git import GitRepository, delete_workspace_tree

WORKSPACES_DIR = os.getenv("WORKSPACES_DIR", "/workspaces")

_WINDOWS_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_WINDOWS_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE)

RESERVED_FRONTMATTER_KEYS = frozenset(
    {"id", "title", "tags", "created_at", "updated_at", "occurred_at", "period"}
)


class InvalidFolderError(ValueError):
    pass


class TemporalMetadataError(ValueError):
    """Invalid or conflicting occurred_at/period input. Distinct from a bare ValueError
    so callers (the API layer) can map it to 422 instead of the generic not-found 404."""


@dataclass(frozen=True, slots=True)
class NoteFrontmatter:
    """Parsed note frontmatter: reserved keys plus everything else in ``extras``.

    ``extras`` cannot shadow a reserved key — validated here so a bad state is rejected
    where it is built, not where it is next written. Dates deliberately keep PyYAML's
    ``str | datetime`` behavior; no coercion is added here.
    """

    id: str | None
    title: str | None
    tags: list[str]
    created_at: str | datetime | None
    updated_at: str | datetime | None
    occurred_at: str | None = field(default=None, kw_only=True)
    period: str | None = field(default=None, kw_only=True)
    extras: dict[str, object] = field(default_factory=dict)
    # Names ("occurred_at"/"period") whose on-disk value parse_frontmatter had to drop to
    # None because it was corrupted — as opposed to genuinely absent from the file. A
    # read-modify-write must consult this before treating occurred_at/period as "unchanged
    # value read from the file", or it silently persists the drop (#132 follow-up).
    temporal_dropped: frozenset[str] = field(default_factory=frozenset, kw_only=True)

    def __post_init__(self) -> None:
        shadowed = RESERVED_FRONTMATTER_KEYS & self.extras.keys()
        if shadowed:
            raise ValueError(f"extras cannot shadow reserved frontmatter keys: {sorted(shadowed)}")
        occurred_at, period = normalize_temporal_metadata(self.occurred_at, self.period)
        if (occurred_at, period) != (self.occurred_at, self.period):
            raise ValueError("occurred_at and period must use canonical string values.")


@dataclass(frozen=True, slots=True)
class ScannedNote:
    """Frontmatter data needed to rebuild the derived note index.

    ``tags`` is already normalized to a list by ``read_note_file``/``parse_frontmatter``.
    """

    note_id: str | None
    title: str | None
    tags: list[str]
    created_at: str | datetime | None
    updated_at: str | datetime | None
    occurred_at: str | None
    period: str | None
    content: str
    folder: str


def title_to_windows_filename(title: str) -> str:
    result = _WINDOWS_FORBIDDEN.sub(" ", title)
    result = re.sub(r" +", " ", result)
    result = result.strip().rstrip(". ")
    if _WINDOWS_RESERVED.match(result):
        result = "_" + result
    if not result:
        result = "untitled"
    return result[:200]


def path_segments(path: str) -> list[str]:
    """Non-empty segments of a slash-separated folder path (``""`` -> ``[]``)."""
    return [s for s in path.split("/") if s]


def normalize_folder(folder: str) -> str:
    parts = path_segments(folder.strip())
    for part in parts:
        if part == "..":
            raise ValueError("Invalid folder: '..' not allowed")
    return "/".join(title_to_windows_filename(p) for p in parts)


def list_workspace_folders(workspace_path: str) -> list[str]:
    """List visible workspace folders from disk. Root is represented by an empty string."""
    root = Path(workspace_path).resolve()
    if not root.is_dir():
        return [""]
    folders = [""]
    for path in root.rglob("*"):
        if not path.is_dir():
            continue
        parts = path.relative_to(root).parts
        if any(part.startswith(".") for part in parts):
            continue
        folders.append("/".join(parts))
    return sorted(folders)


def _is_empty_dir(path: Path) -> bool:
    """A directory with no entries at all. A folder holding only ``.gitkeep`` is
    NOT empty — that file marks an intentionally-kept empty folder."""
    return path.is_dir() and not any(path.iterdir())


def prune_empty_parents(ws_path: str, folder: str) -> list[str]:
    """Remove now-empty ancestor dirs of ``folder`` bottom-up, stopping at the first
    non-empty dir (which includes any folder containing ``.gitkeep``) or the root.
    Git does not track directories, so this is a pure filesystem op. Returns the
    folder paths removed."""
    root = Path(ws_path).resolve()
    removed: list[str] = []
    current = folder
    while current:
        target = root / current
        if not _is_empty_dir(target):
            break
        target.rmdir()
        removed.append(current)
        current = str(Path(current).parent) if "/" in current else ""
    return removed


def prune_all_empty_dirs(ws_path: str) -> list[str]:
    """Remove every completely-empty directory in the workspace, bottom-up so
    emptied parents cascade. Skips hidden dirs (e.g. ``.git``) and keeps folders
    holding a ``.gitkeep``. Returns the folder paths removed."""
    root = Path(ws_path).resolve()
    if not root.is_dir():
        return []
    removed: list[str] = []
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        path = Path(dirpath)
        if path == root:
            continue
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if _is_empty_dir(path):
            path.rmdir()
            removed.append("/".join(rel.parts))
    return removed


def remove_empty_tree(ws_path: str, folder: str) -> list[str]:
    """After a folder move, make the source vanish: remove ``folder``'s now-empty
    subtree bottom-up, then its emptied parents. Only touches empty dirs (a leftover
    file keeps its dir). Returns the folder paths removed."""
    root = Path(ws_path).resolve()
    removed: list[str] = []
    if folder:
        sub = Path(root, *path_segments(folder))
        if sub.is_dir():
            dirs = sorted(
                (p for p in sub.rglob("*") if p.is_dir()),
                key=lambda p: len(p.parts),
                reverse=True,
            )
            for path in [*dirs, sub]:
                if _is_empty_dir(path):
                    path.rmdir()
                    removed.append("/".join(path.relative_to(root).parts))
    parent = folder.rsplit("/", 1)[0] if "/" in folder else ""
    removed.extend(prune_empty_parents(ws_path, parent))
    return removed


def workspace_path(name: str, workspaces_dir: str | None = None, *, user_id: str) -> str:
    """Returns the filesystem path for a workspace directory."""
    base = Path(workspaces_dir or os.getenv("WORKSPACES_DIR", "/workspaces"))
    return str(base / user_id / name)


def note_filepath(ws_path: str, folder: str, title: str) -> str:
    filename = title_to_windows_filename(title) + ".md"
    return str(Path(ws_path, *path_segments(folder), filename))


@dataclass(frozen=True, slots=True)
class LocatedNote:
    """A note row resolved to its workspace file, shared by every write/rename path."""

    note: Note
    filepath: str
    relative: str
    file_exists: bool
    head_sha: str | None = None


def locate_note(note: Note, ws_path: str) -> LocatedNote:
    filepath = note_filepath(ws_path, note.folder, note.title)
    return LocatedNote(
        note=note,
        filepath=filepath,
        relative=str(Path(filepath).relative_to(ws_path)),
        file_exists=Path(filepath).exists(),
    )


def relative_folder(root: str | Path, directory: str | Path) -> str:
    """Workspace-relative folder path of ``directory`` (``""`` for the root itself)."""
    return "/".join(Path(directory).relative_to(root).parts)


def note_folder(ws_path: str, path: str | Path) -> str:
    """Inverse of ``note_filepath``: the workspace-relative folder a note file sits in
    (``""`` for the workspace root)."""
    return relative_folder(ws_path, Path(path).parent)


def write_note_file(path: str, meta: NoteFrontmatter, body: str) -> None:
    """Writes ``meta``'s reserved fields and ``extras`` verbatim into ``body``'s frontmatter.

    Builds ``post.metadata`` as a plain dict rather than passing ``extras`` through
    ``Post(**kwargs)`` — an extras key that isn't a string, or that happens to be named
    ``content``/``handler``, would otherwise raise or get silently swallowed by
    ``Post.__init__``'s own parameters.
    """
    post = frontmatter.Post(body)
    post.metadata = {
        "id": meta.id,
        "title": meta.title,
        "tags": meta.tags,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "occurred_at": meta.occurred_at,
        "period": meta.period,
        **meta.extras,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644
    fd, temp_path = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temp = Path(temp_path)
    try:
        os.fchmod(fd, mode)
        stream = os.fdopen(fd, "w")
        fd = -1  # stream owns the descriptor from here
        with stream:
            frontmatter.dump(post, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(target)
    finally:
        if fd >= 0:
            os.close(fd)
        temp.unlink(missing_ok=True)


def _parse_temporal_soft(
    parser: Callable[[object], str | None], value: object, *, note_id: str | None, field: str
) -> tuple[str | None, bool]:
    """Run a strict ``_parse_occurred_at``/``_parse_period`` parser, but on a
    ``TemporalMetadataError`` (a hand-edited or otherwise corrupted value) log a warning
    and return ``(None, True)`` instead of propagating — see ``parse_frontmatter``. The
    ``bool`` distinguishes "dropped a bad value" from "the file legitimately had none",
    so a read-modify-write can fall back to the DB's last-known-good value instead of
    silently persisting the drop (#132 follow-up)."""
    try:
        return parser(value), False
    except TemporalMetadataError as exc:
        logger.warning(
            "frontmatter_temporal_value_ignored", note_id=note_id, field=field, error=str(exc)
        )
        return None, True


def parse_frontmatter(post: frontmatter.Post) -> tuple[NoteFrontmatter, str]:
    """The one place every frontmatter reader turns a parsed ``Post`` into ``(meta, content)``.

    Only a YAML list is a valid ``tags`` value; anything else (a bare scalar from
    hand-edited frontmatter) becomes ``[]`` rather than propagating untyped garbage.
    Every other top-level key becomes ``meta.extras``, verbatim.

    ``occurred_at``/``period`` are parsed leniently: a value a hand edit (or other
    external write) left unparseable — individually, or two otherwise-valid values that
    conflict (both set) — is dropped to ``None`` with a warning instead of raising, so a
    note with corrupted temporal metadata stays readable. The drop is recorded in
    ``meta.temporal_dropped`` so a caller doing a read-modify-write can tell "value was
    corrupted" apart from "value is genuinely absent" instead of silently persisting the
    drop. Writing new values still validates strictly, via ``normalize_temporal_metadata``.
    """
    metadata: dict[str, object] = dict(post.metadata)
    note_id = cast("str | None", metadata.get("id"))
    tags = metadata.pop("tags", [])
    occurred_at, occurred_at_dropped = _parse_temporal_soft(
        _parse_occurred_at, metadata.pop("occurred_at", None), note_id=note_id, field="occurred_at"
    )
    period, period_dropped = _parse_temporal_soft(
        _parse_period, metadata.pop("period", None), note_id=note_id, field="period"
    )
    id_ = cast("str | None", metadata.pop("id", None))
    title = cast("str | None", metadata.pop("title", None))
    created_at = cast("str | datetime | None", metadata.pop("created_at", None))
    updated_at = cast("str | datetime | None", metadata.pop("updated_at", None))
    tags_list = cast("list[str]", list(tags)) if isinstance(tags, list) else []
    temporal_dropped = frozenset(
        name
        for name, dropped in (("occurred_at", occurred_at_dropped), ("period", period_dropped))
        if dropped
    )
    try:
        meta = NoteFrontmatter(
            id=id_,
            title=title,
            tags=tags_list,
            created_at=created_at,
            updated_at=updated_at,
            occurred_at=occurred_at,
            period=period,
            extras=metadata,
            temporal_dropped=temporal_dropped,
        )
    except TemporalMetadataError as exc:
        # Both individually well-formed but simultaneously present — the file is still
        # corrupted (this state is unreachable through the app's own writes), so drop
        # both rather than arbitrating a winner.
        logger.warning("frontmatter_temporal_value_ignored", note_id=note_id, error=str(exc))
        meta = NoteFrontmatter(
            id=id_,
            title=title,
            tags=tags_list,
            created_at=created_at,
            updated_at=updated_at,
            occurred_at=None,
            period=None,
            extras=metadata,
            temporal_dropped=frozenset({"occurred_at", "period"}),
        )
    return meta, post.content


def _parse_occurred_at(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime) or not isinstance(value, (str, date)):
        raise TemporalMetadataError("occurred_at must be an ISO calendar date (YYYY-MM-DD).")
    try:
        parsed = date.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as exc:
        raise TemporalMetadataError(
            "occurred_at must be an ISO calendar date (YYYY-MM-DD)."
        ) from exc
    if parsed.isoformat() != (value if isinstance(value, str) else parsed.isoformat()):
        raise TemporalMetadataError("occurred_at must be an ISO calendar date (YYYY-MM-DD).")
    return parsed.isoformat()


def _parse_period(value: object) -> str | None:
    if value is None:
        return None
    # PyYAML parses a bare 4-digit year like `period: 2026` as int, not str, so a
    # hand-written year period needs the same native-type accommodation _parse_occurred_at
    # gives `date`. bool is an int subclass, so it's excluded explicitly.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise TemporalMetadataError("period must be a canonical period key.")
    try:
        return parse_period_key(str(value)).key
    except ValueError as exc:
        raise TemporalMetadataError("period must be a canonical period key.") from exc


def normalize_temporal_metadata(
    occurred_at: object = None, period: object = None
) -> tuple[str | None, str | None]:
    """Validate and canonicalize the two mutually-exclusive temporal note facts."""
    normalized_occurred_at = _parse_occurred_at(occurred_at)
    normalized_period = _parse_period(period)
    if normalized_occurred_at is not None and normalized_period is not None:
        raise TemporalMetadataError("occurred_at and period are mutually exclusive.")
    return normalized_occurred_at, normalized_period


def temporal_drop_warnings(temporal_dropped: frozenset[str]) -> list[str]:
    """Plain-text warnings for fields ``parse_frontmatter`` had to drop as unparseable
    (see ``NoteFrontmatter.temporal_dropped``) — ``list[str]``, matching the untyped
    ``warnings`` shape callers like ``NoteTagService`` already return (MCP-only, so English
    per ``mcp/CLAUDE.md``), so this is visible in the response instead of only a
    server-side log line (#132 follow-up). Not used for ``NoteService.update``/
    ``edit_many``: their ``warnings`` field is the strictly-typed ``list[WikilinkWarning]``
    (a closed ``kind`` literal) — see ``temporal_drop_warning_payloads`` for those.
    """
    return [
        f"{field}: file value was unparseable — ignored, kept the previous value."
        for field in sorted(temporal_dropped)
    ]


def temporal_drop_warning_payloads(temporal_dropped: frozenset[str]) -> list[dict]:
    """Structured ``{kind, field}`` warnings for the same drop, shaped to validate as
    ``shared.notes.TemporalWarning`` — for ``NoteService.update``/``edit_many``, whose
    ``warnings`` field is typed and reused by both the REST API and MCP, so the message
    text is the consuming schema's job, not this one's (#132 follow-up)."""
    return [
        {"kind": "temporal_value_ignored", "field": field} for field in sorted(temporal_dropped)
    ]


def temporal_kwargs(occurred_at: str | None, period: str | None) -> dict[str, str]:
    """Keyword args for occurred_at/period, omitting unset fields entirely.

    Shared by every caller that must distinguish "leave unchanged" (omitted) from
    "set to this value" before calling NoteService.update, which treats an omitted
    kwarg differently from an explicit None (see NoteService._UNCHANGED).
    """
    return {
        key: value
        for key, value in (("occurred_at", occurred_at), ("period", period))
        if value is not None
    }


def resolve_temporal_fields(
    *,
    has_occurred_at: bool,
    has_period: bool,
    occurred_at: object,
    period: object,
    clear: bool,
    fallback: tuple[str | None, str | None],
) -> tuple[str | None, str | None]:
    """The shared occurred_at/period resolution rule for a note update: clear both,
    set one or both explicitly, or fall back to the caller-supplied unchanged values.
    Used by both NoteService.update and NoteService.edit_many, which differ only in
    how "was this field passed" is detected and what the fallback source is."""
    if clear and (has_occurred_at or has_period):
        raise TemporalMetadataError(
            "clear_date_metadata cannot be combined with occurred_at or period."
        )
    if clear:
        return None, None
    if has_occurred_at and has_period:
        return normalize_temporal_metadata(occurred_at, period)
    if has_occurred_at:
        return normalize_temporal_metadata(occurred_at, None)
    if has_period:
        return normalize_temporal_metadata(None, period)
    return fallback


def read_note_file(path: str) -> tuple[NoteFrontmatter, str]:
    return parse_frontmatter(frontmatter.load(path))


def iter_note_paths(workspace_path: str) -> list[str]:
    """Every ``.md`` file's workspace-relative path, sorted for deterministic order.
    Does not read file content — used where only the path set matters (reconcile)."""
    ws = Path(workspace_path)
    if not ws.exists():
        return []
    return [str(p.relative_to(ws)) for p in sorted(ws.rglob("*.md")) if ".git" not in p.parts]


def scan_notes(workspace_path: str) -> list[ScannedNote]:
    ws = Path(workspace_path)
    results = []
    for relative in iter_note_paths(workspace_path):
        p = ws / relative
        meta, content = read_note_file(str(p))
        results.append(
            ScannedNote(
                note_id=meta.id,
                title=meta.title,
                tags=meta.tags,
                created_at=meta.created_at,
                updated_at=meta.updated_at,
                occurred_at=meta.occurred_at,
                period=meta.period,
                content=content,
                folder=note_folder(workspace_path, p),
            )
        )
    return results


def create_workspace(name: str, workspaces_dir: str | None = None, *, user_id: str) -> str:
    """Creates a new workspace directory with git repo. Returns the workspace path."""
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,49}$", name):
        raise ValueError(
            f"Invalid workspace name '{name}'."
            " Use letters, digits, hyphens, underscores (max 50 chars)."
        )

    ws_path = Path(workspace_path(name, workspaces_dir=workspaces_dir, user_id=user_id))

    if ws_path.exists():
        raise FileExistsError(f"Workspace '{name}' already exists.")

    ws_path.parent.mkdir(parents=True, exist_ok=True)
    GitRepository.init(str(ws_path))
    return str(ws_path)


def delete_workspace_directory(
    name: str, workspaces_dir: str | None = None, *, user_id: str
) -> None:
    """Removes a workspace's on-disk git repo. Idempotent."""
    ws_path = workspace_path(name, workspaces_dir=workspaces_dir, user_id=user_id)
    delete_workspace_tree(ws_path)
