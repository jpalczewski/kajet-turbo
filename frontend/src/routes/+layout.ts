import { apiSessionGetApiSessionGet } from '$lib/api';
import type { LayoutLoad } from './$types';

export const ssr = false;

export const load: LayoutLoad = async ({ depends }) => {
  depends('app:session');
  const result = await apiSessionGetApiSessionGet().catch(() => null);
  return { session: result?.data ?? null };
};
