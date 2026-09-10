import type { UserPreferences } from '$lib/api';

// created_at / updated_at / last_commit_at are instants: format with DateFormatPrefs
// (formatDate, formatDateTime, formatUnixDate, formatUnixDateTime). occurred_at is a
// floating calendar value ('YYYY-MM-DD', no instant, no zone): format with formatPlainDate,
// which parses the components directly instead of going through Date's UTC-midnight path.
// period ('YYYY-Www') is also floating but has no frontend renderer yet — extend
// formatPlainDate's parsing (or add a sibling) when the first caller appears, don't
// pass an ISO week string to formatPlainDate as-is.
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

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
