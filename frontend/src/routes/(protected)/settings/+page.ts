import type { PageLoad } from './$types';
import { loadApi } from '$lib/api/load';
import {
  apiListEmbeddingProfilesApiMeEmbeddingProfilesGet,
  apiListSshKeysApiMeSshKeysGet,
} from '$lib/api';

export const load: PageLoad = async () => {
  const [profiles, keys] = await Promise.all([
    loadApi(apiListEmbeddingProfilesApiMeEmbeddingProfilesGet(), 'Nie znaleziono ustawień.'),
    loadApi(apiListSshKeysApiMeSshKeysGet(), 'Nie znaleziono kluczy SSH.'),
  ]);
  return { profiles: profiles.data.profiles, keys: keys.data.keys };
};
