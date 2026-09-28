import { apiNoteGraphApiWorkspacesNameNotesGraphGet } from '$lib/api';
import { loadApi } from '$lib/api/load';
import { workspacesPath } from '$lib/routes';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params, url }) => {
  const slug = params.slug;
  const includeTags = url.searchParams.get('tags') === '1';

  const result = await loadApi(
    apiNoteGraphApiWorkspacesNameNotesGraphGet(slug, { include_tags: includeTags }),
    'Nie znaleziono workspace’u.',
    { forbiddenRedirect: workspacesPath() },
  );

  return {
    slug,
    graph: result.data,
    includeTags,
  };
};
