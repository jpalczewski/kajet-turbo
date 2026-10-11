import { apiListCollectionsApiWorkspacesNameCollectionsGet } from '$lib/api';
import { loadApi } from '$lib/api/load';
import type { LayoutLoad } from './$types';
import { loadKeys } from '$lib/invalidation';

export const load: LayoutLoad = async ({ params, depends }) => {
  // Definitions live in the workspace repo, so they refresh with the tree events.
  depends(loadKeys.workspaceTree(params.slug));
  depends(loadKeys.collections);
  const result = await loadApi(
    apiListCollectionsApiWorkspacesNameCollectionsGet(params.slug),
    'Workspace nie istnieje.',
  );
  return { slug: params.slug, collections: result.data.collections };
};
