import { normalizeTag } from '$lib/tags';

export type TagOption = { value: string; isCreate: boolean };

/** Normalized, deduped suggestion paths that aren't already applied. */
export function computeCandidates(tags: string[], suggestions: string[]): string[] {
  const seen = new Set(tags);
  const out: string[] = [];
  for (const raw of suggestions) {
    const n = normalizeTag(raw);
    if (n && !seen.has(n)) {
      seen.add(n);
      out.push(n);
    }
  }
  return out;
}

/**
 * Dropdown options for the current query: up to 8 substring matches, plus a
 * "create" option when the query is a fresh, valid tag that isn't an exact
 * existing match and isn't already applied.
 */
export function computeOptions(query: string, candidates: string[], tags: string[]): TagOption[] {
  const normalizedQuery = normalizeTag(query);
  const needle = normalizedQuery ?? '';
  const matches = needle ? candidates.filter((c) => c.includes(needle)).slice(0, 8) : [];
  const opts: TagOption[] = matches.map((value) => ({ value, isCreate: false }));
  if (normalizedQuery && !tags.includes(normalizedQuery) && !matches.includes(normalizedQuery)) {
    opts.push({ value: normalizedQuery, isCreate: true });
  }
  return opts;
}
