import { error, redirect } from '@sveltejs/kit';
import { loginPath } from '$lib/routes';

/**
 * Awaits an orval client call inside a load function and maps API failures to
 * SvelteKit responses: 401 -> login redirect, 403/404 -> 404 page, anything
 * else -> 500. The fetcher throws on non-OK with `status` attached, so errors
 * arrive here as exceptions, not result objects.
 */
export async function loadApi<T extends { status: number }>(
  promise: Promise<T>,
  notFound: string,
): Promise<T & { status: 200 }> {
  let result: T;
  try {
    result = await promise;
  } catch (e) {
    const status = (e as { status?: number }).status;
    if (status === 401) redirect(307, loginPath());
    if (status === 403 || status === 404) error(404, notFound);
    error(500, 'Błąd serwera.');
  }
  if (result.status !== 200) error(500, 'Błąd serwera.');
  return result as T & { status: 200 };
}
