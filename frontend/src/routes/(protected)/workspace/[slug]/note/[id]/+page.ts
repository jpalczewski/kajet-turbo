import {
  apiGetNoteHtmlApiWorkspacesNameNotesNoteIdHtmlGet,
  apiNoteLinksApiWorkspacesNameNotesNoteIdLinksGet,
} from '$lib/api';
import { loadApi } from '$lib/api/load';
import type { PageLoad } from './$types';
import { loadKeys } from '$lib/invalidation';

export const load: PageLoad = async ({ params, depends }) => {
  depends(loadKeys.note(params.id));
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
