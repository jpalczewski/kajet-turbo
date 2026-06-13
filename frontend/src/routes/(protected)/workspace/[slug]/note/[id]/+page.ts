import {
  apiGetNoteHtmlApiWorkspacesNameNotesNoteIdHtmlGet,
  apiNoteBacklinksApiWorkspacesNameNotesNoteIdBacklinksGet,
} from '$lib/api';
import { loadApi } from '$lib/api/load';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
  const [note, backlinks] = await Promise.all([
    loadApi(
      apiGetNoteHtmlApiWorkspacesNameNotesNoteIdHtmlGet(params.slug, params.id),
      'Notatka nie istnieje.',
    ),
    loadApi(
      apiNoteBacklinksApiWorkspacesNameNotesNoteIdBacklinksGet(params.slug, params.id),
      'Notatka nie istnieje.',
    ),
  ]);
  return { note: note.data, backlinks: backlinks.data.backlinks };
};
