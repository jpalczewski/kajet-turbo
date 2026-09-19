import { apiListNotesApiWorkspacesNameNotesGet } from '$lib/api';
import { loadApi } from '$lib/api/load';
import type { PageLoad } from './$types';

// The endpoint accepts up to 500; a "recent" view has no use for more than a screenful or two.
const RECENT_LIMIT = 50;

export const load: PageLoad = async ({ params, depends }) => {
  // note_updated events invalidate this key (see (protected)/+layout.svelte), so an edit
  // anywhere reorders the list without a refresh.
  depends('app:workspace-tree');
  const result = await loadApi(
    apiListNotesApiWorkspacesNameNotesGet(params.slug, { sort: 'updated', limit: RECENT_LIMIT }),
    'Workspace nie istnieje.',
  );
  return { slug: params.slug, notes: result.data.notes };
};
