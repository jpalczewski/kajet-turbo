import { translateErrorCode } from './errors';

/** Extracts and translates the backend error code, falling back to the provided message. */
export function apiErrorMessage(e: unknown, fallback: string): string {
  const data = (e as { data?: { error?: string } }).data;
  if (data?.error) return translateErrorCode(data.error) ?? fallback;
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}
