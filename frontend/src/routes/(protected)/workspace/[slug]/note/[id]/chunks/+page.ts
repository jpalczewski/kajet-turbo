import { apiGetNoteChunksApiWorkspacesNameNotesNoteIdChunksGet } from '$lib/api';
import { loadApi } from '$lib/api/load';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
  const result = await loadApi(
    apiGetNoteChunksApiWorkspacesNameNotesNoteIdChunksGet(params.slug, params.id),
    'Notatka nie istnieje.',
  );
  return { preview: result.data, slug: params.slug, noteId: params.id };
};
