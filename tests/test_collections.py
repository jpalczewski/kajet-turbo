from datetime import date
from pathlib import Path

import pytest

from kajet_turbo.collections import load_collections


def test_load_collections_renders_week_month_by_iso_thursday(tmp_path: Path):
    config = tmp_path / ".kajet"
    config.mkdir()
    (config / "collections.yaml").write_text(
        "weekly:\n"
        "  grain: week\n"
        "  cardinality: one\n"
        "  folder: weekly/{year}/{month}\n"
        "  title: '{key}'\n",
        encoding="utf-8",
    )
    collection = load_collections(str(tmp_path))["weekly"]
    folder, title = collection.render(date(2026, 1, 1))
    assert (folder, title) == ("weekly/2026/01", "2026-W01")


def test_collections_reject_many_without_ordinal(tmp_path: Path):
    config = tmp_path / ".kajet"
    config.mkdir()
    (config / "collections.yaml").write_text(
        "sessions:\n"
        "  grain: day\n"
        "  cardinality: many\n"
        "  folder: sessions/{year}\n"
        "  title: '{date}'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"requires \{ordinal\}"):
        load_collections(str(tmp_path))
