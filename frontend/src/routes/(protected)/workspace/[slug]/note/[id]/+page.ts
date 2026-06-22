import {
  apiGetNoteHtmlApiWorkspacesNameNotesNoteIdHtmlGet,
  apiNoteLinksApiWorkspacesNameNotesNoteIdLinksGet,
} from '$lib/api';
import { loadApi } from '$lib/api/load';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
  const [note, links] = await Promise.all([
    loadApi(
      apiGetNoteHtmlApiWorkspacesNameNotesNoteIdHtmlGet(params.slug, params.id),
      'Notatka nie istnieje.',
    ),
    loadApi(
      apiNoteLinksApiWorkspacesNameNotesNoteIdLinksGet(params.slug, params.id),
      'Notatka nie istnieje.',
    ),
  ]);
  return { note: note.data, backlinks: links.data.backlinks, outlinks: links.data.outlinks };
};
