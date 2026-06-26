"""Per-workspace settings registry — the single source of truth.

Each setting is declared once as a SettingDef; API, MCP, validation, and the
frontend all derive from REGISTRY. Values are stored as a JSON blob on
WorkspaceMeta.settings; the DB is dumb, this module is the typed gate.
"""

from dataclasses import dataclass
from typing import Literal

SettingType = Literal["bool"]


@dataclass(frozen=True)
class SettingDef:
    key: str
    type: SettingType
    default: object
    label: str
    description: str


REGISTRY: dict[str, SettingDef] = {
    "validate_links": SettingDef(
        key="validate_links",
        type="bool",
        default=True,
        label="Walidacja linków",
        description=(
            "Odrzucaj zapis notatki z linkiem do nieistniejącej notatki. "
            "Wyłączone: linki do brakujących notatek zostają jako dangling i "
            "rozwiązują się same, gdy notatka docelowa powstanie."
        ),
    ),
}


def _check_type(defn: SettingDef, value: object) -> object:
    if defn.type == "bool":
        # bool only — no int/str coercion. isinstance(True, int) is True in
        # Python, so guard against ints explicitly via the exact-bool check.
        if not isinstance(value, bool):
            raise ValueError(f"Setting {defn.key!r} expects a bool, got {type(value).__name__}.")
        return value
    raise ValueError(f"Unsupported setting type {defn.type!r}.")  # pragma: no cover


def validate(key: str, value: object) -> object:
    """Validate/coerce a single setting value. Raises ValueError on bad key/type."""
    defn = REGISTRY.get(key)
    if defn is None:
        raise ValueError(f"Unknown setting {key!r}.")
    return _check_type(defn, value)


def coerce_all(raw: dict | None) -> dict:
    """Full settings dict: drop unknown keys, fill missing with defaults."""
    raw = raw or {}
    out: dict = {}
    for key, defn in REGISTRY.items():
        out[key] = raw.get(key, defn.default)
    return out


def defaults() -> dict:
    return {key: defn.default for key, defn in REGISTRY.items()}


def definitions() -> list[dict]:
    return [
        {
            "key": d.key,
            "type": d.type,
            "label": d.label,
            "description": d.description,
            "default": d.default,
        }
        for d in REGISTRY.values()
    ]
