import { apiSessionGetApiSessionGet } from '$lib/api';
import { loadApiOrNull } from '$lib/api/load';
import type { LayoutLoad } from './$types';

export const ssr = false;

export const load: LayoutLoad = async ({ depends }) => {
  depends('app:session');
  return { session: await loadApiOrNull(apiSessionGetApiSessionGet()) };
};
