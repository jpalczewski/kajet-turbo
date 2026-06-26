import type { PageLoad } from './$types';
import { loadApi } from '$lib/api/load';
import { apiListSshKeysApiMeSshKeysGet } from '$lib/api';

export const load: PageLoad = async () => {
  const keys = await loadApi(apiListSshKeysApiMeSshKeysGet(), 'Nie znaleziono kluczy SSH.');
  return { keys: keys.data.keys };
};
