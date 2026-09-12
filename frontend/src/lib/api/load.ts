import { error, redirect } from '@sveltejs/kit';
import { loginPath } from '$lib/routes';

export type LoadApiOptions = {
  /** Redirect to this path instead of rendering the default 404 for a 403 response. */
  forbiddenRedirect?: string;
  /** Render this message as a 400 response instead of the default 500. */
  badRequest?: string;
};

/**
 * Awaits an orval client call inside a load function and maps API failures to
 * SvelteKit responses: 401 -> login redirect, 403/404 -> 404 page, and
 * anything else -> 500. Callers can override 403 with a redirect and 400 with
 * an expected error. The fetcher throws on non-OK with `status` attached, so
 * errors arrive here as exceptions, not result objects.
 */
export async function loadApi<T extends { status: number }>(
  promise: Promise<T>,
  notFound: string,
  options: LoadApiOptions = {},
): Promise<T & { status: 200 }> {
  let result: T;
  try {
    result = await promise;
  } catch (e) {
    const status = (e as { status?: number } | null)?.status;
    if (status === 401) redirect(307, loginPath());
    if (status === 400 && options.badRequest) error(400, options.badRequest);
    if (status === 403 && options.forbiddenRedirect) redirect(307, options.forbiddenRedirect);
    if (status === 403 || status === 404) error(404, notFound);
    error(500, 'Błąd serwera.');
  }
  if (result.status !== 200) error(500, 'Błąd serwera.');
  return result as T & { status: 200 };
}
