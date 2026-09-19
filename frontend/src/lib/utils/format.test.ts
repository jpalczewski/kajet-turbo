import { describe, expect, it } from 'vitest';
import {
  DEFAULT_DATE_PREFS,
  formatDate,
  formatDateTime,
  formatPlainDate,
  formatPlainMonthName,
  formatPlainMonthYear,
  formatPlainWeek,
  formatRelative,
  formatUnixDate,
  formatUnixDateTime,
  isoWeekSpan,
  type DateFormatPrefs,
} from './format';

const warsaw = { timezone: 'Europe/Warsaw', locale: 'pl' as const };
const kiritimati = { timezone: 'Pacific/Kiritimati', locale: 'pl' as const };

describe('formatUnixDateTime', () => {
  it('renders the same instant as a different calendar day depending on the zone', () => {
    // 2026-01-01T22:00:00Z: still the 1st in Warsaw (+01:00), already the 2nd in Kiritimati (+14:00)
    const ts = Date.UTC(2026, 0, 1, 22, 0, 0) / 1000;
    const inWarsaw = formatUnixDateTime(ts, warsaw);
    const inKiritimati = formatUnixDateTime(ts, kiritimati);
    expect(inWarsaw).toBe('01.01.2026, 23:00');
    expect(inKiritimati).toBe('02.01.2026, 12:00');
    expect(inWarsaw.split(',')[0]).not.toBe(inKiritimati.split(',')[0]);
  });
});

describe('formatDate / formatDateTime', () => {
  it('formats an ISO instant in the given zone and locale', () => {
    expect(formatDate('2026-03-05T10:00:00Z', warsaw)).toBe('05.03.2026');
    expect(formatDateTime('2026-03-05T10:00:00Z', warsaw)).toBe('05.03.2026, 11:00');
  });

  it('returns an empty string for an empty input', () => {
    expect(formatDate('', warsaw)).toBe('');
    expect(formatDateTime('', warsaw)).toBe('');
  });
});

describe('formatUnixDate', () => {
  it('returns an empty string for null/undefined/0', () => {
    expect(formatUnixDate(null, warsaw)).toBe('');
    expect(formatUnixDate(undefined, warsaw)).toBe('');
    expect(formatUnixDate(0, warsaw)).toBe('');
  });
});

describe('formatPlainDate', () => {
  it('is unaffected by the viewer timezone — a floating date has no instant', () => {
    expect(formatPlainDate('2026-09-04', { timezone: 'America/Los_Angeles', locale: 'pl' })).toBe(
      '04.09.2026',
    );
    expect(formatPlainDate('2026-09-04', kiritimati)).toBe('04.09.2026');
  });

  it('returns an empty string for an empty input', () => {
    expect(formatPlainDate('', warsaw)).toBe('');
  });
});

describe('isoWeekSpan', () => {
  it('resolves a week to Monday, Thursday and Sunday', () => {
    expect(isoWeekSpan('2026-W36')).toEqual({
      week: 36,
      start: '2026-08-31',
      thursday: '2026-09-03',
      end: '2026-09-06',
    });
  });

  it('starts week 1 in the previous calendar year when 4 January is late in the week', () => {
    expect(isoWeekSpan('2026-W01')?.start).toBe('2025-12-29');
    expect(isoWeekSpan('2027-W01')?.start).toBe('2027-01-04');
  });

  it('rejects malformed keys and out-of-range weeks', () => {
    expect(isoWeekSpan('2026-36')).toBeNull();
    expect(isoWeekSpan('2026-W00')).toBeNull();
    expect(isoWeekSpan('2026-W54')).toBeNull();
    expect(isoWeekSpan('')).toBeNull();
  });
});

describe('plain period formatters', () => {
  const la = { timezone: 'America/Los_Angeles', locale: 'pl' as const };

  it('are unaffected by the viewer timezone', () => {
    expect(formatPlainMonthYear(2026, 9, la)).toBe('wrzesień 2026');
    expect(formatPlainMonthYear(2026, 9, kiritimati)).toBe('wrzesień 2026');
    expect(formatPlainMonthName(1, la)).toBe('styczeń');
    expect(formatPlainWeek('2026-W36', kiritimati)).toBe('W36 · 31.08.2026 – 06.09.2026');
  });

  it('follow the locale', () => {
    expect(formatPlainMonthYear(2026, 9, { ...la, locale: 'en' })).toBe('September 2026');
  });

  it('formatPlainWeek echoes a malformed key', () => {
    expect(formatPlainWeek('garbage', la)).toBe('garbage');
  });
});

describe('formatRelative', () => {
  const now = Date.UTC(2026, 8, 19, 12, 0, 0);
  const ago = (ms: number) => new Date(now - ms).toISOString();
  const MIN = 60 * 1000;
  const HOUR = 60 * MIN;
  const rel = (ms: number, prefs: DateFormatPrefs = warsaw) => formatRelative(ago(ms), prefs, now);

  it('says "now" under a minute, and treats a timestamp ahead of the clock the same way', () => {
    expect(rel(20 * 1000)).toBe('teraz');
    expect(rel(-5 * MIN)).toBe('teraz');
  });

  it('inflects Polish minutes: one / few / many', () => {
    expect(rel(1 * MIN)).toBe('1 minutę temu');
    expect(rel(2 * MIN)).toBe('2 minuty temu');
    expect(rel(5 * MIN)).toBe('5 minut temu');
    expect(rel(22 * MIN)).toBe('22 minuty temu');
  });

  it('inflects Polish hours: one / few / many', () => {
    expect(rel(1 * HOUR)).toBe('1 godzinę temu');
    expect(rel(2 * HOUR)).toBe('2 godziny temu');
    expect(rel(5 * HOUR)).toBe('5 godzin temu');
    expect(rel(22 * HOUR)).toBe('22 godziny temu');
  });

  it('counts days on the calendar of the user zone, not in 24h blocks', () => {
    // now = 2026-09-19 14:00 in Warsaw. 27h earlier is 2026-09-18 11:00: yesterday.
    expect(rel(27 * HOUR)).toBe('wczoraj');
    // 40h earlier is 2026-09-17 22:00: the day before yesterday, though under 48h.
    expect(rel(40 * HOUR)).toBe('przedwczoraj');
    expect(rel(5 * 24 * HOUR)).toBe('5 dni temu');
  });

  it('gives the same instant a different day label depending on the zone', () => {
    // now = 2026-09-19 14:00 in Warsaw but already 2026-09-20 02:00 in Kiritimati (+14);
    // 33h earlier is 09-18 05:00 in Warsaw (yesterday) and 09-18 17:00 in Kiritimati (two days).
    expect(rel(33 * HOUR, warsaw)).toBe('wczoraj');
    expect(rel(33 * HOUR, kiritimati)).toBe('przedwczoraj');
  });

  it('falls through to weeks, months and years', () => {
    expect(rel(14 * 24 * HOUR)).toBe('2 tygodnie temu');
    expect(rel(70 * 24 * HOUR)).toBe('2 miesiące temu');
    expect(rel(800 * 24 * HOUR)).toBe('2 lata temu');
  });

  it('follows the locale', () => {
    const en = { ...warsaw, locale: 'en' as const };
    expect(rel(2 * HOUR, en)).toBe('2 hours ago');
    expect(rel(27 * HOUR, en)).toBe('yesterday');
  });

  it('returns an empty string for empty or malformed input', () => {
    expect(formatRelative('', warsaw, now)).toBe('');
    expect(formatRelative('not a date', warsaw, now)).toBe('');
  });
});

describe('DEFAULT_DATE_PREFS', () => {
  it('matches the backend defaults', () => {
    expect(DEFAULT_DATE_PREFS).toEqual({ timezone: 'Europe/Warsaw', locale: 'pl' });
  });
});
