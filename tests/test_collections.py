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


# --- sibling_pattern (open_entry ordinal allocation, #115) --------------------


def test_sibling_pattern_matches_ordinal_in_folder():
    definition = _daily_many(folder="sessions/{year}/{ordinal}", title="{date}")
    pattern = definition.sibling_pattern(date(2026, 6, 1))
    folder, title = definition.render(date(2026, 6, 1), ordinal=3)
    match = pattern.match(f"{folder}/{title}")
    assert match is not None
    assert match.groups() == ("3",)


def test_sibling_pattern_matches_ordinal_in_title():
    definition = _daily_many(folder="sessions/{year}", title="{date} {ordinal}")
    pattern = definition.sibling_pattern(date(2026, 6, 1))
    folder, title = definition.render(date(2026, 6, 1), ordinal=12)
    match = pattern.match(f"{folder}/{title}")
    assert match is not None
    assert match.groups() == ("12",)


def test_sibling_pattern_matches_padded_ordinal():
    definition = _daily_many(folder="sessions/{year}", title="{date} {ordinal:03d}")
    pattern = definition.sibling_pattern(date(2026, 6, 1))
    folder, title = definition.render(date(2026, 6, 1), ordinal=7)
    assert title.endswith("007")
    match = pattern.match(f"{folder}/{title}")
    assert match is not None
    assert int(match.group(1)) == 7


def test_sibling_pattern_rejects_other_dates_and_collections():
    definition = _daily_many(folder="sessions/{year}", title="{date} {ordinal}")
    pattern = definition.sibling_pattern(date(2026, 6, 1))
    other_date_folder, other_date_title = definition.render(date(2026, 6, 2), ordinal=1)
    assert pattern.match(f"{other_date_folder}/{other_date_title}") is None
    assert pattern.match("sessions/2026/unrelated note") is None


# --- matches (list_entries membership, #116) -----------------------------------


def _journal(folder="journal/{year}/{month}", title="{date}"):
    return CollectionDefinition("journal", "day", "one", folder, title)


def test_matches_true_for_rendered_pair():
    definition = _journal()
    folder, title = definition.render(date(2026, 6, 15))
    assert definition.matches(folder, title) is True


def test_matches_is_not_bounded_by_render_sets_sampling_window():
    # render_set() only samples +/- a few years around "today" — matches() must not
    # inherit that limit, since a real workspace holds entries far outside it.
    definition = _journal()
    far_past = date(1994, 5, 2)
    far_future = date(2099, 12, 31)
    assert definition.matches(*definition.render(far_past)) is True
    assert definition.matches(*definition.render(far_future)) is True


def test_matches_bare_year_month_template_is_also_not_bounded():
    # Same guarantee as test_matches_is_not_bounded_by_render_sets_sampling_window,
    # but for a template with no {date}/{key} at all — {year}/{month} alone must still
    # recover the date exactly for day/month/year grain, not fall back to sampling.
    definition = _journal(folder="archive/{year}", title="{month}-{ordinal}")
    far_past_folder, far_past_title = definition.render(date(1994, 5, 2), ordinal=1)
    assert definition.matches(far_past_folder, far_past_title) is True


def test_matches_month_grain_bare_year_month():
    definition = CollectionDefinition("monthly", "month", "one", "archive/{year}", "{month}")
    far_past_folder, far_past_title = definition.render(date(1994, 5, 1))
    assert far_past_folder == "archive/1994"
    assert far_past_title == "05"
    assert definition.matches(far_past_folder, far_past_title) is True


def test_matches_year_grain_bare_year():
    definition = CollectionDefinition("yearly", "year", "one", "archive", "{year}")
    far_past_folder, far_past_title = definition.render(date(1994, 5, 1))
    assert definition.matches(far_past_folder, far_past_title) is True


def test_matches_week_grain_bare_year_month_falls_back_to_render_set():
    # Week grain's {month} comes from the ISO-Thursday convention (month_of_week), not
    # the calendar month of an arbitrary day in the week, so _recover_when excludes
    # week grain from the {year}/{month} guess (it could land in a different ISO week
    # than the one actually rendered). This must fall back to render_set instead —
    # not crash, not silently mismatch. Mon 2025-12-29 is ISO week 2026-W01, so its
    # folder is "weekly/2026/01", exercising exactly that month-shift.
    definition = _weekly(folder="weekly/{year}/{month}", title="{ordinal}")
    folder, title = definition.render(date(2025, 12, 29), ordinal=1)
    assert folder == "weekly/2026/01"
    assert definition.matches(folder, title) is True
    assert definition.matches("weekly/2026/01", "999") is False


def test_matches_week_grain_via_key():
    definition = _weekly(folder="weekly/{year}", title="{key}")
    folder, title = definition.render(date(2026, 6, 1))
    assert definition.matches(folder, title) is True


def test_matches_week_grain_with_month_round_trips_iso_thursday():
    definition = _weekly(folder="weekly/{year}/{month}", title="{key}")
    # Mon 2025-12-29 is in 2026-W01; its Thursday is Jan 1 -> month 01, year 2026.
    folder, title = definition.render(date(2025, 12, 29))
    assert (folder, title) == ("weekly/2026/01", "2026-W01")
    assert definition.matches(folder, title) is True


def test_matches_many_cardinality_round_trips_ordinal():
    definition = _daily_many(folder="sessions/{year}", title="{date} {ordinal}")
    folder, title = definition.render(date(2026, 6, 15), ordinal=4)
    assert definition.matches(folder, title) is True
    # A different ordinal for the same date is a different, equally real member.
    assert definition.matches(folder, "2026-06-15 5") is True
    # Non-numeric text where {ordinal} must be digits is not a member at all.
    assert definition.matches(folder, "2026-06-15 fifth") is False


def test_matches_false_for_wrong_shape():
    definition = _journal()
    assert definition.matches("elsewhere", "2026-06-15") is False
    assert definition.matches("journal/2026/06", "not a date") is False


def test_matches_false_for_shape_matching_but_invalid_date():
    definition = _journal()
    # "13" and "45" fit \d{2} but there is no such month or day.
    assert definition.matches("journal/2026/13", "2026-13-45") is False


def test_matches_false_for_mutually_inconsistent_duplicate_field():
    # {year} appears in both folder and title; only the first occurrence is trusted to
    # recover `when`, so a mismatched second occurrence must fail the round-trip check.
    definition = CollectionDefinition("odd", "day", "one", "log/{year}", "{date} ({year})")
    real_folder, real_title = definition.render(date(2026, 6, 15))
    assert real_title.endswith("(2026)")
    tampered_title = real_title.replace("(2026)", "(2027)")
    assert definition.matches(real_folder, tampered_title) is False
