import {
  apiGetNoteMarkdownApiWorkspacesNameNotesNoteIdMarkdownGet,
  apiListTagsApiWorkspacesNameTagsGet,
} from '$lib/api';
import { loadApi, loadApiOrNull } from '$lib/api/load';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
  const [result, tags] = await Promise.all([
    loadApi(
      apiGetNoteMarkdownApiWorkspacesNameNotesNoteIdMarkdownGet(params.slug, params.id),
      'Notatka nie istnieje.',
    ),
    loadApiOrNull(apiListTagsApiWorkspacesNameTagsGet(params.slug)),
  ]);
  const allTags = tags?.tags.map((t) => t.path) ?? [];
  return { note: result.data, slug: params.slug, allTags };
};
