import { translateErrorCode } from './errors';

/** Builds RequestInit for orval mutation calls, which take bodies via options. */
export const jsonBody = (payload: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
});

/** Extracts and translates the backend error code, falling back to the provided message. */
export function apiErrorMessage(e: unknown, fallback: string): string {
  const data = (e as { data?: { error?: string } }).data;
  if (data?.error) return translateErrorCode(data.error) ?? fallback;
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}
