import { error, redirect } from '@sveltejs/kit';
import { apiNoteHistoryApiWorkspacesNameNotesNoteIdHistoryGet } from '$lib/api';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
  const result = await apiNoteHistoryApiWorkspacesNameNotesNoteIdHistoryGet(
    params.slug,
    params.id,
    { credentials: 'include' },
  ).catch(() => null);

  const status = result?.status as number | undefined;
  if (status === 401) redirect(307, '/login');
  if (status === 403 || status === 404) error(404, 'Notatka nie istnieje.');
  if (!result || status !== 200) error(500, 'Błąd serwera.');

  const data = result.data as any;
  return { entries: data.entries ?? [], noteId: params.id, slug: params.slug };
};
