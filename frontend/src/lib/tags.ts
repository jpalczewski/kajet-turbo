// Mirrors the backend tag normalization (kajet_turbo/tags.py): bare, lowercased,
// slash-separated segments of unicode word chars / hyphen. Returns null if invalid/empty.
const TAG_PATH_RE = /^[\p{L}\p{N}_-]+(?:\/[\p{L}\p{N}_-]+)*$/u;

export function normalizeTag(raw: string): string | null {
  const stripped = raw.trim().replace(/^#+/, '').trim();
  const segments = stripped.split('/').filter(Boolean);
  if (segments.length === 0) return null;
  const path = segments.join('/').toLowerCase();
  return TAG_PATH_RE.test(path) ? path : null;
}
