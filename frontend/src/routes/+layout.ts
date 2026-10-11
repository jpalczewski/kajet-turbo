import { apiSessionGetApiSessionGet } from '$lib/api';
import { loadApiOrNull } from '$lib/api/load';
import type { LayoutLoad } from './$types';
import { loadKeys } from '$lib/invalidation';

export const ssr = false;

export const load: LayoutLoad = async ({ depends }) => {
  depends(loadKeys.session);
  return { session: await loadApiOrNull(apiSessionGetApiSessionGet()) };
};
