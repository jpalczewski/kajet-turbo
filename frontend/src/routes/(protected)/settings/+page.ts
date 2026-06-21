import type { PageLoad } from './$types';
import { loadApi } from '$lib/api/load';
import { apiEmbeddingBackendsApiEmbeddingBackendsGet } from '$lib/api';

export const load: PageLoad = async () => {
  const res = await loadApi(
    apiEmbeddingBackendsApiEmbeddingBackendsGet(),
    'Nie znaleziono ustawień.',
  );
  return { embedding: res.data };
};
