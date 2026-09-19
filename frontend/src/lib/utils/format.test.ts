import { describe, expect, it } from 'vitest';
import {
  DEFAULT_DATE_PREFS,
  formatDate,
  formatDateTime,
  formatPlainDate,
  formatPlainMonthName,
  formatPlainMonthYear,
  formatPlainWeek,
  formatUnixDate,
  formatUnixDateTime,
  isoWeekSpan,
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

describe('DEFAULT_DATE_PREFS', () => {
  it('matches the backend defaults', () => {
    expect(DEFAULT_DATE_PREFS).toEqual({ timezone: 'Europe/Warsaw', locale: 'pl' });
  });
});
