import { apiGetNoteMarkdownApiWorkspacesNameNotesNoteIdMarkdownGet } from '$lib/api';
import { loadApi } from '$lib/api/load';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
  const result = await loadApi(
    apiGetNoteMarkdownApiWorkspacesNameNotesNoteIdMarkdownGet(params.slug, params.id),
    'Notatka nie istnieje.',
  );
  return { note: result.data, slug: params.slug };
};
