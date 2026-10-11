import { error } from '@sveltejs/kit';
import { apiListCollectionEntriesApiWorkspacesNameCollectionsCollectionEntriesGet } from '$lib/api';
import { loadApi } from '$lib/api/load';
import type { PageLoad } from './$types';
import { loadKeys } from '$lib/invalidation';

const NOT_FOUND = 'Kolekcja nie istnieje.';

export const load: PageLoad = async ({ params, parent, depends }) => {
  depends(loadKeys.workspaceTree(params.slug));
  depends(loadKeys.collection(params.collection));
  // Entries carry no grain or cardinality; those come from the definition the layout loaded.
  const [{ collections }, entries] = await Promise.all([
    parent(),
    loadApi(
      apiListCollectionEntriesApiWorkspacesNameCollectionsCollectionEntriesGet(
        params.slug,
        params.collection,
      ),
      NOT_FOUND,
    ),
  ]);
  const definition = collections.find((c) => c.name === params.collection);
  if (!definition) error(404, NOT_FOUND);
  return { definition, notes: entries.data.notes };
};
