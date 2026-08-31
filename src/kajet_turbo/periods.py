"""Pure calendar-period arithmetic built on Gregorian dates and ISO weeks."""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

PeriodKind = Literal["day", "week", "month", "year"]

_PERIOD_KINDS = frozenset(("day", "week", "month", "year"))


@dataclass(frozen=True, order=True)
class Period:
    """A canonical day, ISO week, calendar month, or calendar/ISO year.

    ``year`` contains days and months by their Gregorian year, but weeks by their
    ISO year. A week has no containing month; use :func:`month_of_week` when a
    folder naming convention needs one.
    """

    kind: PeriodKind
    key: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, str)
            or self.kind not in _PERIOD_KINDS
            or not isinstance(self.key, str)
        ):
            raise ValueError(f"Invalid period: {self.kind!r}, {self.key!r}.")
        try:
            parsed = self._parse_key()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {self.kind} period key {self.key!r}.") from exc
        if self._format_key(parsed) != self.key:
            raise ValueError(f"Invalid {self.kind} period key {self.key!r}.")

    @classmethod
    def containing(cls, d: date, kind: PeriodKind) -> Period:
        """Return the period of ``kind`` containing the calendar date ``d``."""
        if type(d) is not date:
            raise ValueError("d must be a date, not a datetime.")
        if kind == "day":
            return cls(kind, d.isoformat())
        if kind == "week":
            iso = d.isocalendar()
            return cls(kind, f"{iso.year:04d}-W{iso.week:02d}")
        if kind == "month":
            return cls(kind, f"{d.year:04d}-{d.month:02d}")
        if kind == "year":
            return cls(kind, f"{d.year:04d}")
        raise ValueError(f"Invalid period kind {kind!r}.")

    def next(self) -> Period:
        """Return the immediately following period of the same kind."""
        if self.kind == "day":
            return self.containing(self._start() + timedelta(days=1), "day")
        if self.kind == "week":
            return self.containing(self._start() + timedelta(weeks=1), "week")
        if self.kind == "month":
            start = self._start()
            if start.month == 12:
                year, month = start.year + 1, 1
            else:
                year, month = start.year, start.month + 1
            return Period("month", f"{year:04d}-{month:02d}")
        start = self._start()
        return Period("year", f"{start.year + 1:04d}")

    def prev(self) -> Period:
        """Return the immediately preceding period of the same kind."""
        if self.kind == "day":
            return self.containing(self._start() - timedelta(days=1), "day")
        if self.kind == "week":
            return self.containing(self._start() - timedelta(weeks=1), "week")
        if self.kind == "month":
            start = self._start()
            if start.month == 1:
                year, month = start.year - 1, 12
            else:
                year, month = start.year, start.month - 1
            return Period("month", f"{year:04d}-{month:02d}")
        start = self._start()
        return Period("year", f"{start.year - 1:04d}")

    @property
    def start(self) -> date:
        """First calendar date in this period."""
        return self._start()

    def contains(self, other: Period) -> bool:
        """Whether ``other`` belongs to this period in the supported hierarchy."""
        if self.kind == "day":
            return other.kind == "day" and self == other
        if self.kind == "week":
            return (
                self == other
                if other.kind == "week"
                else other.kind == "day" and self == self.containing(other._start(), "week")
            )
        if self.kind == "month":
            if other.kind == "week":
                raise ValueError("A week has no containing month; use month_of_week().")
            if other.kind == "month":
                return self == other
            return other.kind == "day" and self.key == other.key[:7]
        if other.kind == "year":
            return self == other
        if other.kind == "week":
            return self.key == other.key[:4]
        if other.kind == "month":
            return self.key == other.key[:4]
        return self.key == other.key[:4]

    def _parse_key(self) -> date:
        if self.kind == "day":
            return date.fromisoformat(self.key)
        if self.kind == "week":
            if len(self.key) != 8 or self.key[4:6] != "-W":
                raise ValueError
            return date.fromisocalendar(int(self.key[:4]), int(self.key[6:]), 1)
        if self.kind == "month":
            if len(self.key) != 7 or self.key[4] != "-":
                raise ValueError
            return date(int(self.key[:4]), int(self.key[5:]), 1)
        return date(int(self.key), 1, 1)

    def _format_key(self, parsed: date) -> str:
        if self.kind == "day":
            return parsed.isoformat()
        if self.kind == "week":
            iso = parsed.isocalendar()
            return f"{iso.year:04d}-W{iso.week:02d}"
        if self.kind == "month":
            return f"{parsed.year:04d}-{parsed.month:02d}"
        return f"{parsed.year:04d}"

    def _start(self) -> date:
        return self._parse_key()


def parse_period_key(key: str) -> Period:
    """Parse an unambiguous canonical period key into its :class:`Period`."""
    if not isinstance(key, str):
        raise ValueError("period must be a canonical period key.")
    if len(key) == 10:
        return Period("day", key)
    if len(key) == 8 and key[4:6] == "-W":
        return Period("week", key)
    if len(key) == 7:
        return Period("month", key)
    if len(key) == 4:
        return Period("year", key)
    raise ValueError(f"Invalid period key {key!r}.")


def month_of_week(w: Period) -> Period:
    """Return the calendar month holding ISO week's Thursday.

    This is a naming convention, not a containment relation.
    """
    if w.kind != "week":
        raise ValueError("w must be a week period.")
    return Period.containing(w._start() + timedelta(days=3), "month")
