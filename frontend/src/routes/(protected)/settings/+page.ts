import type { PageLoad } from './$types';
import { loadApi } from '$lib/api/load';
import { apiListEmbeddingProfilesApiMeEmbeddingProfilesGet } from '$lib/api';

export const load: PageLoad = async () => {
  const res = await loadApi(
    apiListEmbeddingProfilesApiMeEmbeddingProfilesGet(),
    'Nie znaleziono ustawień.',
  );
  return { profiles: res.data.profiles };
};
