from datetime import date
from typing import cast

import pytest

from kajet_turbo.periods import Period, PeriodKind, month_of_week


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("day", "2026-02-28"),
        ("week", "2020-W53"),
        ("month", "2026-02"),
        ("year", "2026"),
    ],
)
def test_period_accepts_canonical_keys(kind: PeriodKind, key: str):
    assert Period(kind, key).key == key


@pytest.mark.parametrize(
    ("kind", "key"),
    [
        ("day", "20260228"),
        ("week", "2025-W53"),
        ("week", "2026-W1"),
        ("month", "2026-13"),
        ("year", "26"),
        ("quarter", "2026-Q1"),
    ],
)
def test_period_rejects_noncanonical_or_invalid_keys(kind: str, key: str):
    with pytest.raises(ValueError, match="Invalid"):
        Period(cast(PeriodKind, kind), key)


def test_containing_uses_iso_week_year_at_calendar_year_boundary():
    day = date(2026, 1, 1)

    assert Period.containing(day, "day") == Period("day", "2026-01-01")
    assert Period.containing(day, "week") == Period("week", "2026-W01")
    assert Period.containing(day, "month") == Period("month", "2026-01")
    assert Period.containing(day, "year") == Period("year", "2026")


def test_navigation_crosses_calendar_and_iso_boundaries():
    assert Period("day", "2024-02-29").next() == Period("day", "2024-03-01")
    assert Period("month", "2025-12").next() == Period("month", "2026-01")
    assert Period("month", "2026-01").prev() == Period("month", "2025-12")
    assert Period("year", "2025").next() == Period("year", "2026")
    assert Period("week", "2020-W53").next() == Period("week", "2021-W01")
    assert Period("week", "2021-W01").prev() == Period("week", "2020-W53")


def test_contains_only_supported_period_hierarchy():
    day = Period("day", "2025-12-29")
    week = Period("week", "2026-W01")
    month = Period("month", "2025-12")
    iso_year = Period("year", "2026")
    calendar_year = Period("year", "2025")

    assert day.contains(day)
    assert week.contains(day)
    assert month.contains(day)
    assert calendar_year.contains(day)
    assert calendar_year.contains(month)
    assert not iso_year.contains(month)
    assert iso_year.contains(week)
    assert not calendar_year.contains(week)
    assert not week.contains(month)
    with pytest.raises(ValueError, match="month_of_week"):
        month.contains(week)


def test_month_of_week_uses_thursday_not_monday():
    week = Period("week", "2026-W14")  # Monday 30 March; Thursday 2 April.

    assert month_of_week(week) == Period("month", "2026-04")
    assert month_of_week(Period("week", "2026-W01")) == Period("month", "2026-01")
    assert month_of_week(Period("week", "2020-W53")) == Period("month", "2020-12")


def test_month_of_week_requires_week_period():
    with pytest.raises(ValueError, match="week period"):
        month_of_week(Period("month", "2026-04"))
