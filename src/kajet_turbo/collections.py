"""Read and validate workspace collection definitions.

Collections deliberately live in the workspace Git repository, not in SQLite.  This
module has no persistence of its own: callers load ``.kajet/collections.yaml`` on
demand and future write tools will use :class:`GitRepository` for mutations.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from string import Formatter
from typing import Literal, cast

import yaml

from kajet_turbo.periods import Period, PeriodKind, month_of_week
from kajet_turbo.workspace import normalize_folder, title_to_windows_filename

Cardinality = Literal["one", "many"]
_FIELDS = frozenset(("grain", "cardinality", "folder", "title"))
_PLACEHOLDERS = frozenset(("date", "key", "year", "month", "ordinal"))


@dataclass(frozen=True, slots=True)
class CollectionDefinition:
    name: str
    grain: PeriodKind
    cardinality: Cardinality
    folder: str
    title: str

    def render(self, when: date, ordinal: int | None = None) -> tuple[str, str]:
        """Render the collection path and title for a date.

        ``ordinal`` is intentionally accepted here although creation belongs to #115:
        it lets validation prove the configured pattern is legal without duplicating
        the period-to-placeholder convention later.
        """
        period = Period.containing(when, self.grain)
        values: dict[str, object] = {
            "date": when.isoformat(),
            "key": period.key,
            "year": period.key[:4],
            "ordinal": ordinal if ordinal is not None else 1,
        }
        if self.grain == "week":
            values["month"] = month_of_week(period).key[5:]
        elif self.grain != "year":
            values["month"] = f"{when.month:02d}"
        return self.folder.format(**values), self.title.format(**values)


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
    missing = _FIELDS - set(values)
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
    if grain not in ("day", "week", "month", "year"):
        raise ValueError(f"Collection '{name}': grain must be day, week, month, or year.")
    if cardinality not in ("one", "many"):
        raise ValueError(f"Collection '{name}': cardinality must be one or many.")
    if not isinstance(folder, str) or not isinstance(title, str):
        raise ValueError(f"Collection '{name}': folder and title must be strings.")
    definition = CollectionDefinition(
        name.strip(), cast("PeriodKind", grain), cast("Cardinality", cardinality), folder, title
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
