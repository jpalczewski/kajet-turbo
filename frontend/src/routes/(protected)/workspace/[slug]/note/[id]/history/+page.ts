import { apiNoteHistoryApiWorkspacesNameNotesNoteIdHistoryGet } from '$lib/api';
import { loadApi } from '$lib/api/load';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
  const result = await loadApi(
    apiNoteHistoryApiWorkspacesNameNotesNoteIdHistoryGet(params.slug, params.id),
    'Notatka nie istnieje.',
  );
  return { entries: result.data.entries ?? [], noteId: params.id, slug: params.slug };
};
