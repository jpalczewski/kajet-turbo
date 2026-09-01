from datetime import date
from pathlib import Path

import pytest

from kajet_turbo.collections import (
    CollectionDefinition,
    _static_prefix,
    collides,
    dropped_members,
    dump_collections,
    load_collections,
    render_set,
)


def _write(tmp_path: Path, text: str) -> None:
    config = tmp_path / ".kajet"
    config.mkdir(exist_ok=True)
    (config / "collections.yaml").write_text(text, encoding="utf-8")


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


def _weekly(folder="weekly/{year}", title="{key}", description=None):
    return CollectionDefinition("weekly", "week", "one", folder, title, description)


def _daily_many(folder="sessions/{year}/{ordinal}", title="{date}", description=None):
    return CollectionDefinition("sessions", "day", "many", folder, title, description)


# --- description is optional -------------------------------------------------


def test_description_optional_loads_without_it(tmp_path: Path):
    _write(
        tmp_path,
        "weekly:\n  grain: week\n  cardinality: one\n  folder: weekly/{year}\n  title: '{key}'\n",
    )
    collection = load_collections(str(tmp_path))["weekly"]
    assert collection.description is None


def test_description_round_trips_when_given(tmp_path: Path):
    _write(
        tmp_path,
        "weekly:\n"
        "  grain: week\n"
        "  cardinality: one\n"
        "  folder: weekly/{year}\n"
        "  title: '{key}'\n"
        "  description: Weekly review notes\n",
    )
    collection = load_collections(str(tmp_path))["weekly"]
    assert collection.description == "Weekly review notes"


@pytest.mark.parametrize("bad_description", ["", "   ", 5, ["not", "a", "string"]])
def test_description_rejects_empty_or_non_string(tmp_path: Path, bad_description):
    _write(
        tmp_path,
        "weekly:\n"
        "  grain: week\n"
        "  cardinality: one\n"
        "  folder: weekly/{year}\n"
        "  title: '{key}'\n"
        f"  description: {bad_description!r}\n",
    )
    with pytest.raises(ValueError, match="description"):
        load_collections(str(tmp_path))


# --- dump_collections round-trips through load_collections --------------------


def test_dump_collections_round_trips(tmp_path: Path):
    definitions = {
        "weekly": _weekly(description="Weekly review notes"),
        "sessions": _daily_many(),
    }
    _write(tmp_path, dump_collections(definitions))
    reloaded = load_collections(str(tmp_path))
    assert reloaded == definitions


def test_dump_collections_omits_absent_description(tmp_path: Path):
    _write(tmp_path, dump_collections({"weekly": _weekly()}))
    assert "description" not in (tmp_path / ".kajet" / "collections.yaml").read_text()


# --- render_set -----------------------------------------------------------------


def test_render_set_cardinality_one_has_one_entry_per_period():
    from kajet_turbo.collections import _SAMPLE_HORIZON_YEARS
    from kajet_turbo.periods import Period

    definition = _weekly(folder="weekly/{year}-{key}", title="{key}")
    anchor = date(2026, 6, 1)
    rendered = render_set(definition, today=anchor)

    # cardinality="one" folds every ordinal to the same rendered pair, so the size
    # equals the number of distinct weeks sampled, not weeks * any ordinal count —
    # count them independently via Period.next() rather than trusting render_set's
    # own iteration to prove its own size.
    start = Period.containing(date(anchor.year - _SAMPLE_HORIZON_YEARS, 1, 1), "week")
    end = Period.containing(date(anchor.year + _SAMPLE_HORIZON_YEARS, 12, 31), "week")
    expected = 1
    period = start
    while period != end:
        period = period.next()
        expected += 1

    assert len(rendered) == expected
    assert ("weekly/2026-2026-W23", "2026-W23") in rendered


def test_render_set_cardinality_many_enumerates_every_ordinal():
    definition = _daily_many(folder="sessions/{date}", title="{date}-{ordinal}")
    rendered = render_set(definition, today=date(2026, 6, 1))
    titles_for_day = {title for _, title in rendered if title.startswith("2026-06-01")}
    assert titles_for_day == {f"2026-06-01-{i}" for i in range(1, 21)}


# --- collides ---------------------------------------------------------------


def test_collides_false_for_different_literal_roots():
    a = _weekly(folder="weekly/{year}")
    b = _weekly(folder="daily/{year}")
    assert collides(a, b) is False


def test_collides_false_for_different_segment_counts():
    a = _weekly(folder="weekly/{year}")
    b = _weekly(folder="weekly/{year}/{month}")
    assert collides(a, b) is False


def test_collides_true_for_identical_folder_templates():
    a = _weekly(folder="weekly/{year}")
    b = CollectionDefinition("other-weekly", "week", "one", "weekly/{year}", "{key}")
    assert collides(a, b) is True


def test_collides_true_across_different_grains_sharing_a_folder_shape():
    # Same folder template, different grain: a week-grain and a year-grain collection
    # both render "archive/2026" for some period — the structural rule-out can't
    # decide this from the templates alone since it doesn't know the two periodizations
    # collapse to the same folder; only sampling catches it.
    weekly = CollectionDefinition("weekly-archive", "week", "one", "archive/{year}", "{key}")
    yearly = CollectionDefinition("yearly-archive", "year", "one", "archive/{year}", "{key}")
    assert collides(weekly, yearly) is True


# --- dropped_members ---------------------------------------------------------


def test_dropped_members_reports_only_pairs_present_in_old_and_absent_in_new():
    old = _weekly(folder="weekly/{year}", title="{key}")
    new = _weekly(folder="weekly-v2/{year}", title="{key}")
    old_pair = old.render(date(2026, 6, 1))
    new_pair = new.render(date(2026, 6, 1))
    unrelated_pair = ("elsewhere", "Not in either")
    dropped = dropped_members(old, new, [old_pair, new_pair, unrelated_pair])
    assert dropped == [old_pair]


def test_dropped_members_empty_when_pair_still_matches_new_definition():
    definition = _weekly(folder="weekly/{year}", title="{key}")
    pair = definition.render(date(2026, 6, 1))
    assert dropped_members(definition, definition, [pair]) == []


# --- _static_prefix -----------------------------------------------------------


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("weekly", "weekly"),
        ("weekly/archive", "weekly/archive"),
        ("{year}", ""),
        ("{year}/journal", ""),
        ("journal/{year}", "journal"),
        ("journal-{year}", ""),
        ("journal/{year}/{month}", "journal"),
    ],
)
def test_static_prefix(template, expected):
    assert _static_prefix(template) == expected
