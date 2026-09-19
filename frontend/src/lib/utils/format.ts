import type { UserPreferences } from '$lib/api';

// created_at / updated_at / last_commit_at are instants: format with DateFormatPrefs
// (formatDate, formatDateTime, formatUnixDate, formatUnixDateTime). occurred_at is a
// floating calendar value ('YYYY-MM-DD', no instant, no zone): format with formatPlainDate,
// which parses the components directly instead of going through Date's UTC-midnight path.
// period ('YYYY-Www', 'YYYY-MM', 'YYYY') is also floating: use isoWeekSpan /
// formatPlainWeek / formatPlainMonthYear / formatPlainMonthName below, and never pass a
// period string to formatPlainDate.
export type DateFormatPrefs = Pick<UserPreferences, 'timezone' | 'locale'>;

export const DEFAULT_DATE_PREFS: DateFormatPrefs = {
  timezone: 'Europe/Warsaw',
  locale: 'pl',
};

export function formatDate(iso: string, prefs: DateFormatPrefs): string {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString(prefs.locale, {
    timeZone: prefs.timezone,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
}

export function formatDateTime(iso: string, prefs: DateFormatPrefs): string {
  if (!iso) return '';
  return new Date(iso).toLocaleString(prefs.locale, {
    timeZone: prefs.timezone,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatUnixDate(ts: number | null | undefined, prefs: DateFormatPrefs): string {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleDateString(prefs.locale, {
    timeZone: prefs.timezone,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
}

export function formatUnixDateTime(ts: number, prefs: DateFormatPrefs): string {
  return new Date(ts * 1000).toLocaleDateString(prefs.locale, {
    timeZone: prefs.timezone,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// value is a floating date ('YYYY-MM-DD', no instant, no zone) — prefs.timezone is
// intentionally unused: formatting always happens in UTC against UTC-built components,
// so the calendar day printed matches the string regardless of the viewer's zone.
export function formatPlainDate(value: string, prefs: DateFormatPrefs): string {
  if (!value) return '';
  const [year, month, day] = value.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString(prefs.locale, {
    timeZone: 'UTC',
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
}

// Floating calendar periods ('YYYY-Www', 'YYYY-MM'): same rule as formatPlainDate — components
// are built with Date.UTC and printed with timeZone 'UTC', so no viewer zone can shift them.

const MS_PER_DAY = 24 * 60 * 60 * 1000;
const ISO_WEEK_KEY = /^(\d{4})-W(\d{2})$/;

const toPlainDate = (utcMs: number): string => new Date(utcMs).toISOString().slice(0, 10);

export type IsoWeekSpan = {
  week: number;
  /** Monday, 'YYYY-MM-DD'. */
  start: string;
  /** Thursday: the day that decides which month and year the week belongs to. */
  thursday: string;
  /** Sunday, 'YYYY-MM-DD'. */
  end: string;
};

/** Resolves an ISO week key ('2026-W36') to its calendar days; null if the key is malformed. */
export function isoWeekSpan(key: string): IsoWeekSpan | null {
  const match = ISO_WEEK_KEY.exec(key);
  if (!match) return null;
  const [year, week] = [Number(match[1]), Number(match[2])];
  if (week < 1 || week > 53) return null;
  // 4 January is always in ISO week 1; step back to that week's Monday.
  const jan4 = Date.UTC(year, 0, 4);
  const week1Monday = jan4 - ((new Date(jan4).getUTCDay() + 6) % 7) * MS_PER_DAY;
  const monday = week1Monday + (week - 1) * 7 * MS_PER_DAY;
  return {
    week,
    start: toPlainDate(monday),
    thursday: toPlainDate(monday + 3 * MS_PER_DAY),
    end: toPlainDate(monday + 6 * MS_PER_DAY),
  };
}

/** 'wrzesień 2026' — month is 1-12. */
export function formatPlainMonthYear(year: number, month: number, prefs: DateFormatPrefs): string {
  return new Intl.DateTimeFormat(prefs.locale, {
    timeZone: 'UTC',
    month: 'long',
    year: 'numeric',
  }).format(Date.UTC(year, month - 1, 1));
}

/** 'wrzesień' — month is 1-12. */
export function formatPlainMonthName(month: number, prefs: DateFormatPrefs): string {
  return new Intl.DateTimeFormat(prefs.locale, { timeZone: 'UTC', month: 'long' }).format(
    Date.UTC(2000, month - 1, 1),
  );
}

/** 'W36 · 31.08.2026 – 06.09.2026' for an ISO week key; the key itself if it is malformed. */
export function formatPlainWeek(key: string, prefs: DateFormatPrefs): string {
  const span = isoWeekSpan(key);
  if (!span) return key;
  const range = `${formatPlainDate(span.start, prefs)} – ${formatPlainDate(span.end, prefs)}`;
  return `W${String(span.week).padStart(2, '0')} · ${range}`;
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
