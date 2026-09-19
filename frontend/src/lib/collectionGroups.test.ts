import { describe, expect, it } from 'vitest';
import type { NoteListItem } from '$lib/api';
import { groupEntries, UNDATED_LABEL } from './collectionGroups';

const prefs = { timezone: 'America/Los_Angeles', locale: 'pl' as const };

function note(title: string, temporal: { occurred_at?: string; period?: string } = {}) {
  return {
    note_id: `id-${title}`,
    workspace: 'ws-1',
    owner_id: 'u1',
    title,
    folder: 'c',
    tags: [],
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...temporal,
  } as NoteListItem;
}

const shape = (groups: ReturnType<typeof groupEntries>) =>
  groups.map((g) => [g.label, g.rows.map((r) => r.label)]);

describe('groupEntries — day grain', () => {
  it('groups by month, newest first, without shifting the day in a western zone', () => {
    const groups = groupEntries(
      'day',
      [
        note('2026-08-31', { occurred_at: '2026-08-31' }),
        note('2026-09-04', { occurred_at: '2026-09-04' }),
        note('2026-09-01', { occurred_at: '2026-09-01' }),
      ],
      prefs,
    );
    expect(shape(groups)).toEqual([
      ['wrzesień 2026', ['04.09.2026', '01.09.2026']],
      ['sierpień 2026', ['31.08.2026']],
    ]);
  });

  it('formats headers in the user locale', () => {
    const groups = groupEntries('day', [note('d', { occurred_at: '2026-09-04' })], {
      ...prefs,
      locale: 'en',
    });
    expect(groups[0].label).toBe('September 2026');
  });

  it('keeps several entries of one day in one group, ordinals in numeric order', () => {
    const groups = groupEntries(
      'day',
      [
        note('2026-06-15 10', { occurred_at: '2026-06-15' }),
        note('2026-06-15 2', { occurred_at: '2026-06-15' }),
        note('2026-06-15 1', { occurred_at: '2026-06-15' }),
      ],
      prefs,
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].rows.map((r) => r.note.title)).toEqual([
      '2026-06-15 1',
      '2026-06-15 2',
      '2026-06-15 10',
    ]);
  });
});

describe('groupEntries — week grain', () => {
  it('groups by the month of the ISO Thursday, not the Monday', () => {
    // 2026-W36 runs Mon 31.08 - Sun 06.09, Thursday is 03.09 -> September.
    const groups = groupEntries('week', [note('w', { period: '2026-W36' })], prefs);
    expect(shape(groups)).toEqual([['wrzesień 2026', ['W36 · 31.08.2026 – 06.09.2026']]]);
  });

  it('places 2026-W01 (starts 29.12.2025) in January 2026', () => {
    const groups = groupEntries('week', [note('w', { period: '2026-W01' })], prefs);
    expect(shape(groups)).toEqual([['styczeń 2026', ['W01 · 29.12.2025 – 04.01.2026']]]);
  });

  it('handles a 53-week year and orders weeks newest first across months', () => {
    const groups = groupEntries(
      'week',
      [
        note('a', { period: '2026-W01' }),
        note('b', { period: '2026-W53' }),
        note('c', { period: '2026-W52' }),
      ],
      prefs,
    );
    expect(groups.map((g) => g.label)).toEqual(['grudzień 2026', 'styczeń 2026']);
    expect(groups[0].rows.map((r) => r.note.title)).toEqual(['b', 'c']);
    expect(groups[0].rows[0].label).toBe('W53 · 28.12.2026 – 03.01.2027');
  });
});

describe('groupEntries — month grain', () => {
  it('groups by year with month names as row labels', () => {
    const groups = groupEntries(
      'month',
      [
        note('a', { period: '2025-12' }),
        note('b', { period: '2026-01' }),
        note('c', { period: '2026-09' }),
      ],
      prefs,
    );
    expect(shape(groups)).toEqual([
      ['2026', ['wrzesień', 'styczeń']],
      ['2025', ['grudzień']],
    ]);
  });
});

describe('groupEntries — year grain', () => {
  it('is one flat, header-less list, newest first', () => {
    const groups = groupEntries(
      'year',
      [note('a', { period: '2024' }), note('b', { period: '2026' }), note('c', { period: '2025' })],
      prefs,
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBeNull();
    expect(groups[0].rows.map((r) => r.label)).toEqual(['2026', '2025', '2024']);
  });
});

describe('groupEntries — unusable periods', () => {
  it('puts entries with no field, or a field of the wrong grain, in a trailing undated group', () => {
    const groups = groupEntries(
      'week',
      [
        note('missing'),
        note('wrong-grain', { period: '2026-09' }),
        note('dated', { period: '2026-W36' }),
        note('day-field', { occurred_at: '2026-09-04' }),
      ],
      prefs,
    );
    expect(groups.map((g) => g.label)).toEqual(['wrzesień 2026', UNDATED_LABEL]);
    expect(groups[1].rows.map((r) => r.note.title)).toEqual([
      'day-field',
      'missing',
      'wrong-grain',
    ]);
  });

  it('returns no groups for no entries', () => {
    expect(groupEntries('day', [], prefs)).toEqual([]);
  });
});
