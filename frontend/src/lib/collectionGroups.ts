import type { CollectionResult, NoteListItem } from '$lib/api';
import {
  formatPlainDate,
  formatPlainMonthName,
  formatPlainMonthYear,
  formatPlainWeek,
  isoWeekSpan,
  type DateFormatPrefs,
} from '$lib/utils/format';

export type CollectionGrain = CollectionResult['grain'];

export type CollectionRow = {
  note: NoteListItem;
  /** The entry's period, formatted. For undated entries: the note title. */
  label: string;
};

export type CollectionGroup = {
  key: string;
  /** null = no header (year grain is a flat list). */
  label: string | null;
  rows: CollectionRow[];
};

export const UNDATED_LABEL = 'Bez daty';

type Placement = {
  groupKey: string;
  groupLabel: string | null;
  /** Lexicographically sortable within a grain: 'YYYY-MM-DD', 'YYYY-Www', 'YYYY-MM', 'YYYY'. */
  sortKey: string;
  label: string;
};

const DAY = /^(\d{4})-(\d{2})-\d{2}$/;
const MONTH = /^(\d{4})-(\d{2})$/;
const YEAR = /^\d{4}$/;

// Grain decides which field carries the entry's period: day -> occurred_at, everything
// coarser -> period. Lenient backend parsing can leave either empty or malformed, so every
// case returns null instead of throwing.
function place(
  grain: CollectionGrain,
  note: NoteListItem,
  prefs: DateFormatPrefs,
): Placement | null {
  switch (grain) {
    case 'day': {
      const day = note.occurred_at;
      const match = day ? DAY.exec(day) : null;
      if (!day || !match) return null;
      return {
        groupKey: `${match[1]}-${match[2]}`,
        groupLabel: formatPlainMonthYear(Number(match[1]), Number(match[2]), prefs),
        sortKey: day,
        label: formatPlainDate(day, prefs),
      };
    }
    case 'week': {
      const period = note.period;
      const span = period ? isoWeekSpan(period) : null;
      if (!period || !span) return null;
      // The Thursday decides the month, matching the backend's {month} placeholder.
      const [year, month] = span.thursday.split('-').map(Number);
      return {
        groupKey: span.thursday.slice(0, 7),
        groupLabel: formatPlainMonthYear(year, month, prefs),
        sortKey: period,
        label: formatPlainWeek(period, prefs),
      };
    }
    case 'month': {
      const period = note.period;
      const match = period ? MONTH.exec(period) : null;
      if (!period || !match) return null;
      return {
        groupKey: match[1],
        groupLabel: match[1],
        sortKey: period,
        label: formatPlainMonthName(Number(match[2]), prefs),
      };
    }
    case 'year': {
      const period = note.period;
      if (!period || !YEAR.test(period)) return null;
      return { groupKey: '', groupLabel: null, sortKey: period, label: period };
    }
  }
}

const byTitle = (a: NoteListItem, b: NoteListItem) =>
  a.title.localeCompare(b.title, undefined, { numeric: true });

/**
 * Groups a collection's entries by period for display, newest first.
 *
 * day -> by month, week -> by month of the ISO Thursday, month -> by year, year -> one flat
 * group. Several entries may share a period (cardinality "many"); they stay in one group as
 * separate rows, ordered by title with numeric collation so ordinal 2 precedes 10 — the
 * backend sorts titles as plain strings. Entries without a usable period go last.
 */
export function groupEntries(
  grain: CollectionGrain,
  notes: NoteListItem[],
  prefs: DateFormatPrefs,
): CollectionGroup[] {
  const placed = new Map<string, { label: string | null; entries: [Placement, NoteListItem][] }>();
  const undated: CollectionRow[] = [];

  for (const note of notes) {
    const placement = place(grain, note, prefs);
    if (!placement) {
      undated.push({ note, label: note.title });
      continue;
    }
    const group = placed.get(placement.groupKey) ?? { label: placement.groupLabel, entries: [] };
    group.entries.push([placement, note]);
    placed.set(placement.groupKey, group);
  }

  const groups: CollectionGroup[] = [...placed]
    .sort(([a], [b]) => (a < b ? 1 : a > b ? -1 : 0))
    .map(([key, { label, entries }]) => ({
      key,
      label,
      rows: entries
        .sort(([pa, na], [pb, nb]) =>
          pa.sortKey === pb.sortKey ? byTitle(na, nb) : pa.sortKey < pb.sortKey ? 1 : -1,
        )
        .map(([placement, note]) => ({ note, label: placement.label })),
    }));

  if (undated.length > 0) {
    undated.sort((a, b) => byTitle(a.note, b.note));
    groups.push({ key: 'undated', label: UNDATED_LABEL, rows: undated });
  }
  return groups;
}
