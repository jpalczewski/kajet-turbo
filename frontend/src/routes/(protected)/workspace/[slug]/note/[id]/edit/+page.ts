import {
  apiGetNoteMarkdownApiWorkspacesNameNotesNoteIdMarkdownGet,
  apiListTagsApiWorkspacesNameTagsGet,
} from '$lib/api';
import { loadApi } from '$lib/api/load';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
  const [result, tagsResult] = await Promise.all([
    loadApi(
      apiGetNoteMarkdownApiWorkspacesNameNotesNoteIdMarkdownGet(params.slug, params.id),
      'Notatka nie istnieje.',
    ),
    apiListTagsApiWorkspacesNameTagsGet(params.slug).catch(() => null),
  ]);
  const allTags = tagsResult?.status === 200 ? tagsResult.data.tags.map((t) => t.path) : [];
  return { note: result.data, slug: params.slug, allTags };
};
