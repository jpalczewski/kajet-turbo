"""Read, validate, and enumerate workspace collection definitions.

Collections deliberately live in the workspace Git repository, not in SQLite. This
module has no persistence of its own: callers load ``.kajet/collections.yaml`` on
demand; write verbs (``services/collections.py``) use :class:`GitRepository` for
mutations and call back into the pure helpers here for validation, collision
detection, and redefinition-impact analysis.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from string import Formatter
from typing import Literal, cast

import yaml

from kajet_turbo.periods import Period, PeriodKind, month_of_week
from kajet_turbo.workspace import normalize_folder, title_to_windows_filename

Cardinality = Literal["one", "many"]
_REQUIRED_FIELDS = frozenset(("grain", "cardinality", "folder", "title"))
_OPTIONAL_FIELDS = frozenset(("description",))
_FIELDS = _REQUIRED_FIELDS | _OPTIONAL_FIELDS
_PLACEHOLDERS = frozenset(("date", "key", "year", "month", "ordinal"))

# Tuning knobs for render_set()'s sampling: wide enough that a real workspace's
# collisions and redefinition impact are always caught, not a proof for pathological
# templates that only diverge decades out or a same-period entry count above
# _MAX_ORDINAL. Kept small on purpose: day-grain + cardinality="many" enumerates
# 365 * 2 * _SAMPLE_HORIZON_YEARS * _MAX_ORDINAL renders, so this is a real
# runtime/coverage trade-off, not just a correctness one. See render_set()'s docstring.
_SAMPLE_HORIZON_YEARS = 5
_MAX_ORDINAL = 20


@dataclass(frozen=True, slots=True)
class CollectionDefinition:
    name: str
    grain: PeriodKind
    cardinality: Cardinality
    folder: str
    title: str
    description: str | None = None

    def _placeholder_values(self, when: date) -> dict[str, object]:
        period = Period.containing(when, self.grain)
        values: dict[str, object] = {
            "date": when.isoformat(),
            "key": period.key,
            "year": period.key[:4],
        }
        if self.grain == "week":
            values["month"] = month_of_week(period).key[5:]
        elif self.grain != "year":
            values["month"] = f"{when.month:02d}"
        return values

    def render(self, when: date, ordinal: int | None = None) -> tuple[str, str]:
        """Render the collection path and title for a date.

        ``ordinal`` lets validation (and ``open_entry``, #115) prove the configured
        pattern is legal without duplicating the placeholder convention elsewhere.
        """
        values = {
            **self._placeholder_values(when),
            "ordinal": ordinal if ordinal is not None else 1,
        }
        return self.folder.format(**values), self.title.format(**values)

    def sibling_pattern(self, when: date) -> re.Pattern[str]:
        """Regex matching the rendered ``folder/title`` of every entry this definition
        could produce for ``when``, for any ordinal.

        Used by ``open_entry`` (#115) to find this date's existing ``cardinality="many"``
        entries and allocate the next ordinal as their max + 1 — never a reused or
        renumbered value (see the module using this for why). ``{ordinal}`` may appear in
        ``folder``, ``title``, or both (schema allows either); each occurrence becomes its
        own unnamed capture group rather than one shared name, since Python's ``re`` forbids
        reusing a group name — callers take ``max()`` across every captured group instead of
        assuming a single one.
        """
        values = {k: re.escape(str(v)) for k, v in self._placeholder_values(when).items()}

        def pattern_for(template: str) -> str:
            parts: list[str] = []
            for literal, field, _spec, _conv in Formatter().parse(template):
                parts.append(re.escape(literal))
                if field == "ordinal":
                    parts.append(r"(\d+)")
                elif field is not None:
                    parts.append(values[field])
            return "".join(parts)

        return re.compile(f"{pattern_for(self.folder)}/{pattern_for(self.title)}$")

    def matches(self, folder: str, title: str) -> bool:
        """Whether ``(folder, title)`` is an actual member of this collection — exact,
        not the ``render_set()`` sampling approximation ``collides``/``dropped_members``
        use for collision/redefinition-impact checks. That approximation has a bounded
        window (+/- a few years, a capped ordinal range) which is fine for "would this
        ever plausibly collide", but silently misses real entries outside it, which is
        wrong for "is this actually a member" — a workspace can (and does) hold entries
        far older than the sampling window.

        Recovers the candidate date from the match itself — via ``{date}`` or ``{key}``,
        whichever the template carries — then confirms membership by re-rendering that
        date and comparing strings exactly, not just "the shape matches" (this also
        catches a folder/title pair whose date parts are individually well-formed but
        mutually inconsistent, e.g. a hand-edited file). Falls back to the bounded
        ``render_set`` check only for the rare template that uses neither ``{date}`` nor
        ``{key}`` (only ``{year}``/``{month}``), which can't be uniquely inverted to a date.
        """
        pattern, groups = self._match_pattern()
        m = pattern.match(f"{folder}/{title}")
        if m is None:
            return False
        captured: dict[str, str] = {}
        for field, value in zip(groups, m.groups(), strict=True):
            captured.setdefault(field, value)
        try:
            when = self._recover_when(captured)
        except ValueError:
            # The captured text has the right shape (digits in the right places) but
            # isn't a real calendar date/period, e.g. "2026-13-45" — not a member.
            return False
        if when is None:
            return (folder, title) in render_set(self)
        ordinal = int(captured["ordinal"]) if "ordinal" in captured else None
        return self.render(when, ordinal) == (folder, title)

    def _match_pattern(self) -> tuple[re.Pattern[str], list[str]]:
        """Regex for ``matches()``: every placeholder becomes its own capture group,
        typed by its exact format (``{key}`` uses this definition's grain, since that
        fixes its shape unambiguously). Returns the pattern plus the field name each
        group (in order) belongs to — a field used twice yields two groups, not one
        shared name, for the same reason ``sibling_pattern`` does.
        """
        field_pattern = {**_FIELD_PATTERNS, "key": _KEY_PATTERNS[self.grain]}
        groups: list[str] = []

        def pattern_for(template: str) -> str:
            parts: list[str] = []
            for literal, field, _spec, _conv in Formatter().parse(template):
                parts.append(re.escape(literal))
                if field is not None:
                    parts.append(f"({field_pattern[field]})")
                    groups.append(field)
            return "".join(parts)

        pattern = re.compile(f"{pattern_for(self.folder)}/{pattern_for(self.title)}$")
        return pattern, groups

    def _recover_when(self, captured: dict[str, str]) -> date | None:
        """The date a matched ``{date}``/``{key}`` capture implies, or ``None`` when the
        template carries neither and a date can't be uniquely recovered."""
        if "date" in captured:
            return date.fromisoformat(captured["date"])
        if "key" in captured:
            return Period(self.grain, captured["key"]).start
        return None


_FIELD_PATTERNS = {
    "date": r"\d{4}-\d{2}-\d{2}",
    "year": r"\d{4}",
    "month": r"\d{2}",
    "ordinal": r"\d+",
}
_KEY_PATTERNS: dict[PeriodKind, str] = {
    "day": r"\d{4}-\d{2}-\d{2}",
    "week": r"\d{4}-W\d{2}",
    "month": r"\d{4}-\d{2}",
    "year": r"\d{4}",
}


def load_collections(workspace_path: str) -> dict[str, CollectionDefinition]:
    """Load ``.kajet/collections.yaml``; a missing file means no collections."""
    path = Path(workspace_path, ".kajet", "collections.yaml")
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid .kajet/collections.yaml: {exc}") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(".kajet/collections.yaml must contain a mapping of collections.")
    return {name: _parse_definition(name, value) for name, value in raw.items()}


def _parse_definition(name: object, raw: object) -> CollectionDefinition:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Collection names must be non-empty strings.")
    if not isinstance(raw, dict):
        raise ValueError(f"Collection '{name}' must be a mapping.")
    values = cast("dict[str, object]", raw)
    unknown = set(values) - _FIELDS
    missing = _REQUIRED_FIELDS - set(values)
    if unknown or missing:
        details = []
        if missing:
            details.append(f"missing {sorted(missing)}")
        if unknown:
            details.append(f"unknown {sorted(unknown)}")
        raise ValueError(f"Collection '{name}' has " + ", ".join(details) + ".")
    grain = values["grain"]
    cardinality = values["cardinality"]
    folder = values["folder"]
    title = values["title"]
    description = values.get("description")
    if grain not in ("day", "week", "month", "year"):
        raise ValueError(f"Collection '{name}': grain must be day, week, month, or year.")
    if cardinality not in ("one", "many"):
        raise ValueError(f"Collection '{name}': cardinality must be one or many.")
    if not isinstance(folder, str) or not isinstance(title, str):
        raise ValueError(f"Collection '{name}': folder and title must be strings.")
    if description is not None and (not isinstance(description, str) or not description.strip()):
        raise ValueError(f"Collection '{name}': description must be a non-empty string if given.")
    definition = CollectionDefinition(
        name.strip(),
        cast("PeriodKind", grain),
        cast("Cardinality", cardinality),
        folder,
        title,
        description,
    )
    fields = _template_fields(folder) | _template_fields(title)
    invalid = fields - _PLACEHOLDERS
    if invalid:
        raise ValueError(f"Collection '{name}' uses unknown placeholders: {sorted(invalid)}.")
    if definition.grain == "year" and "month" in fields:
        raise ValueError(f"Collection '{name}': {{month}} is not defined for year grain.")
    if definition.cardinality == "one" and "ordinal" in fields:
        raise ValueError(f"Collection '{name}': {{ordinal}} requires cardinality 'many'.")
    if definition.cardinality == "many" and "ordinal" not in fields:
        raise ValueError(f"Collection '{name}': cardinality 'many' requires {{ordinal}}.")
    _validate_rendered_path(definition)
    return definition


def validate_definition(
    name: str,
    grain: PeriodKind,
    cardinality: Cardinality,
    folder: str,
    title: str,
    description: str | None = None,
) -> CollectionDefinition:
    """Build and validate a :class:`CollectionDefinition` from raw field values — the
    entry point write verbs use, sharing every parsing/validation rule with
    :func:`load_collections`'s YAML path via :func:`_parse_definition`.
    """
    raw: dict[str, object] = {
        "grain": grain,
        "cardinality": cardinality,
        "folder": folder,
        "title": title,
    }
    if description is not None:
        raw["description"] = description
    return _parse_definition(name, raw)


def _template_fields(template: str) -> set[str]:
    try:
        return {field for _, field, _, _ in Formatter().parse(template) if field is not None}
    except ValueError as exc:
        raise ValueError(f"Invalid collection template {template!r}.") from exc


def _validate_rendered_path(definition: CollectionDefinition) -> None:
    # 2026-W01 exercises the ISO-year/month convention at a calendar-year boundary.
    folder, title = definition.render(date(2026, 1, 1), ordinal=1)
    if (
        not folder
        or folder.startswith("/")
        or "\\" in folder
        or any(part in ("", ".", "..") for part in folder.split("/"))
    ):
        raise ValueError(f"Collection '{definition.name}': folder renders to an invalid path.")
    if normalize_folder(folder) != folder:
        raise ValueError(f"Collection '{definition.name}': folder renders to an illegal path.")
    if not title or title_to_windows_filename(title) != title:
        raise ValueError(f"Collection '{definition.name}': title renders to an illegal filename.")


def dump_collections(definitions: dict[str, CollectionDefinition]) -> str:
    """Canonical YAML text for ``.kajet/collections.yaml``. Mirrors :func:`load_collections`:
    ``yaml.safe_dump``, no round-trip/comment preservation (the #105 canonical-form
    decision), insertion order kept, ``description`` omitted rather than written as
    ``description: null`` when absent.
    """
    raw: dict[str, dict[str, str]] = {}
    for name, definition in definitions.items():
        entry = {
            "grain": definition.grain,
            "cardinality": definition.cardinality,
            "folder": definition.folder,
            "title": definition.title,
        }
        if definition.description is not None:
            entry["description"] = definition.description
        raw[name] = entry
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)


def render_set(definition: CollectionDefinition, today: date | None = None) -> set[tuple[str, str]]:
    """Every ``(folder, title)`` pair ``definition`` can render within a sampling
    window around ``today`` (real "now" when not given).

    This operationalizes collection membership: a note belongs to ``definition`` iff
    its ``(folder, title)`` is in this set — the "conjunction rule" collisions and
    redefinition impact are built on. The window is +/- ``_SAMPLE_HORIZON_YEARS``
    years, and cardinality="many" crosses every period with ordinals
    ``1.._MAX_ORDINAL``. This is sound for any realistic collection (workspaces are
    small, and nobody backdates or preplans entries decades out), not an exhaustive
    proof for a pathological template that only diverges outside the window or a
    same-period entry count beyond ``_MAX_ORDINAL``.
    """
    anchor = today if today is not None else date.today()
    start = Period.containing(date(anchor.year - _SAMPLE_HORIZON_YEARS, 1, 1), definition.grain)
    end = Period.containing(date(anchor.year + _SAMPLE_HORIZON_YEARS, 12, 31), definition.grain)
    ordinals: tuple[int | None, ...] = (
        tuple(range(1, _MAX_ORDINAL + 1)) if definition.cardinality == "many" else (None,)
    )
    rendered: set[tuple[str, str]] = set()
    period = start
    while True:
        for ordinal in ordinals:
            rendered.add(definition.render(period.start, ordinal))
        if period == end:
            break
        period = period.next()
    return rendered


def collides(a: CollectionDefinition, b: CollectionDefinition) -> bool:
    """Whether two collections' folder patterns can render the same folder for some
    period, breaking the invariant that a folder identifies at most one collection.
    Only folders matter here — title plays no part in that invariant.

    Two-tier: a fast, exact structural rule-out for genuinely different folder shapes
    (the common case — different literal segments, or a different segment count, can
    never produce the same string), falling back to sampling both patterns' rendered
    folders (:func:`render_set`) only when segment shapes are compatible. The fallback
    is sound within that sampling window, not an exhaustive proof.
    """
    segments_a = a.folder.split("/")
    segments_b = b.folder.split("/")
    if len(segments_a) != len(segments_b):
        return False
    for sa, sb in zip(segments_a, segments_b, strict=True):
        if not _template_fields(sa) and not _template_fields(sb) and sa != sb:
            return False
    folders_a = {folder for folder, _ in render_set(a)}
    folders_b = {folder for folder, _ in render_set(b)}
    return not folders_a.isdisjoint(folders_b)


def dropped_members(
    old: CollectionDefinition,
    new: CollectionDefinition,
    candidates: Iterable[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Which of ``candidates`` (existing notes' ``(folder, title)`` pairs) belonged to
    ``old`` and no longer belong to ``new`` — the redefinition-impact report.
    """
    old_set = render_set(old)
    new_set = render_set(new)
    return [pair for pair in candidates if pair in old_set and pair not in new_set]


def _static_prefix(folder_template: str) -> str:
    """The static path segments of ``folder_template`` before its first templated
    segment — the folder prefix a redefinition-impact query can scope to.

    Segment-wise, not a naive cut at the first ``{``: ``"journal-{year}/x"`` has no
    static segment at all, not ``"journal-"`` — ``list_under_folder`` matches on whole
    path segments, so a partial-segment prefix would never match anything. An empty
    result means the very first segment is templated (``"{year}/journal"``); the
    caller must treat that as "no folder scoping available", not as the literal
    root-only prefix an empty string would mean to ``list_under_folder``.
    """
    segments: list[str] = []
    for segment in folder_template.split("/"):
        if _template_fields(segment):
            break
        segments.append(segment)
    return "/".join(segments)
