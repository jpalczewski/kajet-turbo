import { translateErrorCode } from './errors';

/** Builds RequestInit for orval mutation calls, which take bodies via options. */
export const jsonBody = (payload: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
});

/** Extracts and translates the backend error code, falling back to the provided message. */
export function apiErrorMessage(e: unknown, fallback: string): string {
  const data = (e as { data?: { error?: string } }).data;
  return translateErrorCode(data?.error) ?? fallback;
}
