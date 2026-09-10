import { describe, expect, it } from 'vitest';
import {
  DEFAULT_DATE_PREFS,
  formatDate,
  formatDateTime,
  formatPlainDate,
  formatUnixDate,
  formatUnixDateTime,
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

describe('DEFAULT_DATE_PREFS', () => {
  it('matches the backend defaults', () => {
    expect(DEFAULT_DATE_PREFS).toEqual({ timezone: 'Europe/Warsaw', locale: 'pl' });
  });
});
