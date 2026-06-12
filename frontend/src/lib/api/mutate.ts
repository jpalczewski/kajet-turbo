/** Builds RequestInit for orval mutation calls, which take bodies via options. */
export const jsonBody = (payload: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
});

/**
 * Extracts the backend's error message from a thrown fetcher error.
 * Mirrors the previous raw-fetch behavior: `body.error ?? fallback`.
 */
export function apiErrorMessage(e: unknown, fallback: string): string {
  const data = (e as { data?: { error?: string } }).data;
  return data?.error ?? fallback;
}
